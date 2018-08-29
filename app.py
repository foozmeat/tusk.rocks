import logging
import os
from logging.handlers import TimedRotatingFileHandler

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from flask_mail import Mail, Message
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from mastodon import MastodonIllegalArgumentError, MastodonUnauthorizedError
from requests import Request
from sqlalchemy import exc

from tr.forms import MastodonIDForm, SubmissionForm
from tr.helpers import get_or_create_host, mastodon_api
from tr.models import Post, Settings, User, metadata

app = Flask(__name__)

FORMAT = "%(asctime)-15s [%(filename)s:%(lineno)s : %(funcName)s()] %(message)s"

formatter = logging.Formatter(FORMAT)

# initialize the log handler
logHandler = TimedRotatingFileHandler('logs/app.log', when='D', backupCount=7)
logHandler.setFormatter(formatter)

# set the log handler level
logHandler.setLevel(logging.INFO)
app.logger.addHandler(logHandler)
app.logger.info("Starting up...")

config = os.environ.get('TR_CONFIG', 'config.DevelopmentConfig')
app.config.from_object(config)
mail = Mail(app)

if app.config['SENTRY_DSN']:
    from raven.contrib.flask import Sentry

    sentry = Sentry(app, dsn=app.config['SENTRY_DSN'])

db = SQLAlchemy(metadata=metadata)
migrate = Migrate(app, db)

db.init_app(app)


@app.before_request
def before_request():
    g.m_user = None

    if 'mastodon' in session:
        g.m_user = session['mastodon']

    try:
        db.engine.execute('SELECT 1 from users')
    except exc.SQLAlchemyError as e:
        return f"Song Delivery is unavailable at the moment: {e}", 503

    app.logger.info(session)


@app.route('/', methods=["GET", "POST"])
def index():
    if app.config['MAINTENANCE_MODE']:
        return render_template('maintenance.html.j2')

    mform = MastodonIDForm()
    sform = SubmissionForm()
    preview_data = None
    is_preview = False

    if request.method == 'POST':
        if sform.validate_on_submit():
            post = Post()
            sform.populate_obj(post)

            if request.form["task"] == 'Preview':

                post.validate_song_link()
                sform.share_link.data = post.share_link
                preview_data = post.preview_content()
                is_preview = True

            elif request.form["task"] == 'Send':
                user = db.session.query(User).filter_by(
                        mastodon_user=session['mastodon']['username']
                ).first()
                post.user_id = user.id
                db.session.add(post)
                db.session.commit()
                flash(f"Post created")
                sform = SubmissionForm()

        else:
            for e in sform.errors.items():
                flash(e[1][0])

    return render_template('index.html.j2',
                           mform=mform,
                           sform=sform,
                           app=app,
                           preview_data=preview_data,
                           is_preview=is_preview
                           )


@app.route('/mastodon_login', methods=['POST'])
def mastodon_login():
    form = MastodonIDForm()
    if form.validate_on_submit():

        user_id = form.mastodon_id.data

        if "@" not in user_id:
            flash('Invalid Mastodon ID')
            return redirect(url_for('index'))

        if user_id[0] == '@':
            user_id = user_id[1:]

        username, host = user_id.split('@')

        session['mastodon_host'] = host

        api = mastodon_api(db, app, host)

        if api:
            return redirect(
                    api.auth_request_url(
                            scopes=['read', 'write'],
                            redirect_uris=url_for("mastodon_oauthorized", _external=True)
                    )
            )
        else:
            flash(f"There was a problem connecting to the mastodon server.")
    else:
        flash("Invalid Mastodon ID")

    return redirect(url_for('index'))


@app.route('/mastodon_oauthorized')
def mastodon_oauthorized():
    authorization_code = request.args.get('code')

    if authorization_code is None:
        flash('You denied the request to sign in to Mastodon.')
    else:

        host = session.get('mastodon_host', None)

        app.logger.info(f"Authorization code {authorization_code} for {host}")

        if not host:
            flash('There was an error. Please ensure you allow this site to use cookies.')
            return redirect(url_for('index'))

        session.pop('mastodon_host', None)

        api = mastodon_api(db, app, host)

        try:
            access_code = api.log_in(
                    code=authorization_code,
                    scopes=["read", "write"],
                    redirect_uri=url_for("mastodon_oauthorized", _external=True)
            )
        except MastodonIllegalArgumentError as e:

            flash(f"There was a problem connecting to the mastodon server. The error was {e}")
            return redirect(url_for('index'))

        # app.logger.info(f"Access code {access_code}")

        api.access_code = access_code

        try:
            session['mastodon'] = {
                'host': host,
                'access_code': access_code,
                'username': api.account_verify_credentials()["username"]
            }

        except MastodonUnauthorizedError as e:
            flash(f"There was a problem connecting to the mastodon server. The error was {e}")
            return redirect(url_for('index'))

        user = db.session.query(User).filter_by(
                mastodon_user=session['mastodon']['username']
        ).first()

        if user:
            app.logger.debug("Existing settings found")
        else:

            user = User()
            user.settings = Settings()
            user.mastodon_access_code = session['mastodon']['access_code']
            user.mastodon_user = session['mastodon']['username']
            user.mastodon_host = get_or_create_host(db, app, session['mastodon']['host'])

            db.session.add(user.settings)
            db.session.add(user)
            db.session.commit()

            if app.config.get('MAIL_SERVER', None):

                body = render_template('new_user_email.txt.j2',
                                       user=user)
                msg = Message(subject=f"New {app.config.get('SITE_NAME', None)} user",
                              body=body,
                              recipients=[app.config.get('MAIL_TO', None)])

                try:
                    mail.send(msg)

                except Exception as e:
                    app.logger.error(e)

    return redirect(url_for('index'))


@app.route('/delete', methods=["POST"])
def delete():
    if 'twitter' in session and 'mastodon' in session:
        # look up settings
        user = db.session.query(User).filter_by(
                mastodon_user=session['mastodon']['username'],
        ).first()

        if user:
            app.logger.info(
                    f"Deleting settings for {session['mastodon']['username']}")
            settings = user.settings
            db.session.delete(user)
            db.session.delete(settings)
            db.session.commit()

    return redirect(url_for('logout'))


@app.route('/logout')
def logout():
    session.pop('mastodon', None)
    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run()
