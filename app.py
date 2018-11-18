import logging
import os
from logging.handlers import TimedRotatingFileHandler

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from flask_mail import Mail, Message
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from mastodon import MastodonIllegalArgumentError, MastodonUnauthorizedError
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
logHandler.setLevel(logging.DEBUG)
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

    try:
        db.engine.execute('SELECT 1 from users')
    except exc.SQLAlchemyError as e:
        return f"Song Delivery is unavailable at the moment: {e}", 503

    app.logger.debug(session)


@app.route('/', methods=["GET", "POST"])
def index():
    # if request.form["task"] == 'Preview':

    posts = db.session.query(Post).order_by(Post.updated.desc()).filter_by(posted=True).limit(10)

    return render_template('community.html.j2',
                           app=app,
                           posts=posts
                           )


@app.route('/', methods=["GET", "POST"])
@app.route('/post', methods=["GET", "POST"])
def post():
    if app.config['MAINTENANCE_MODE']:
        return render_template('maintenance.html.j2')

    sform = SubmissionForm()
    preview_data = None
    is_preview = False

    if request.method == 'POST':
        if sform.validate_on_submit():
            post = Post()
            sform.populate_obj(post)

            if request.form["task"] == 'Preview':

                sform.share_link.data = post.share_link
                preview_data = post.preview_content()
                is_preview = True

            elif request.form["task"] == 'Send':
                user = db.session.query(User).filter_by(
                        mastodon_access_code=session['mastodon']['access_code']
                ).first()

                if not user:
                    flash("An error occurred. User not found")
                    return redirect(url_for('post'))

                post.user_id = user.id
                db.session.add(post)
                db.session.commit()
                flash(f"Thank you! Your post will appear soon.")

                if app.config.get('MAIL_SERVER', None):

                    body = render_template('email/new_post.txt.j2',
                                           user=user,
                                           post=post)

                    msg = Message(subject=f"New Post",
                                  body=body,
                                  recipients=[app.config.get('MAIL_TO', None)])

                    try:
                        mail.send(msg)

                    except Exception as e:
                        app.logger.error(e)

                return redirect(url_for('index'))

        else:
            for e in sform.errors.items():
                flash(e[1][0])

    return render_template('post.html.j2',
                           sform=sform,
                           app=app,
                           preview_data=preview_data,
                           is_preview=is_preview
                           )


@app.route('/mastodon_login', methods=['GET', 'POST'])
def mastodon_login():
    form = MastodonIDForm()

    if request.method == 'POST' and form.validate_on_submit():

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
    elif request.method == 'GET':

        return render_template('m_login.html.j2',
                               mform=form,
                               app=app,
                               )

    elif request.method == 'POST':
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
            creds = api.account_verify_credentials()

        except MastodonUnauthorizedError as e:
            flash(f"There was a problem connecting to the mastodon server. The error was {e}")
            return redirect(url_for('index'))

        session['mastodon'] = {
            'host': host,
            'access_code': access_code,
            'username': creds["username"],
            'user_id': creds["id"]
        }

        user = db.session.query(User).filter_by(
                mastodon_access_code=session['mastodon']['access_code']
        ).first()

        if user:
            app.logger.debug("Existing settings found")
            session['user_id'] = user.id

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

                body = render_template('email/new_user_email.txt.j2',
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
                mastodon_access_code=session['mastodon']['access_code']
        ).first()

        if user:
            app.logger.info(
                    f"Deleting settings for {session['mastodon']['username']}")
            settings = user.settings
            db.session.delete(user)
            db.session.delete(settings)
            db.session.commit()

    return redirect(url_for('logout'))


@app.route('/delete_post/<post_id>', methods=["GET"])
def delete_post(post_id):

    post = db.session.query(Post).filter_by(id=post_id).first()

    if not post:
        flash("No post found")
        return redirect(url_for('index'))

    user = db.session.query(User).filter_by(
            mastodon_access_code=session['mastodon']['access_code']
    ).first()

    if post.user_id != user.id:
        flash("Permission Denied")
        return redirect(url_for('index'))

    db.session.delete(post)
    db.session.commit()

    flash("Deleted")
    return redirect(url_for('index'))


@app.route('/logout', methods=["GET", "POST"])
def logout():
    session.pop('mastodon', None)
    return redirect(url_for('index'))


@app.route('/privacy')
def privacy():
    return render_template('privacy.html.j2',
                           app=app)


if __name__ == '__main__':
    app.run()
