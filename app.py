import logging
import os
import re
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from flask_mail import Mail, Message
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from markupsafe import Markup, escape
from mastodon import MastodonIllegalArgumentError, MastodonUnauthorizedError
from pymysql import InternalError
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
app.logger.setLevel(logging.INFO)
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

    posts = db.session.query(Post).order_by(Post.updated.desc()).filter_by(posted=True)

    for p in posts:
        p.fetch_metadata()
        db.session.commit()

    return render_template('community.html.j2',
                           app=app,
                           posts=posts
                           )


@app.route('/post', methods=["GET", "POST"])
def post():
    if app.config['MAINTENANCE_MODE']:
        return render_template('maintenance.html.j2')

    sform = SubmissionForm()
    is_preview = False
    post = None

    if request.method == 'POST':
        if sform.validate_on_submit():
            post = Post()
            sform.populate_obj(post)

            if request.form["task"] == 'Preview':
                post.fetch_metadata()
                sform.share_link.data = post.share_link
                is_preview = True

                if sform.comment.data == "":
                    sform.comment.data = f'{post.title}\n\nSent from https://tusk.rocks 🐘🎸\n'

            elif request.form["task"] == 'Send':

                uid = session.get('user_id', None)

                if uid:

                    user = db.session.query(User).filter_by(id=uid).first()

                    if not user:
                        flash("An error occurred. User not found")
                        return redirect(url_for('post'))
                else:
                    # For some reason sometimes user_id isn't set
                    flash("An error occurred. Please log in again.")
                    return redirect(url_for('logout'))

                post.user_id = user.id
                post.fetch_metadata()
                db.session.add(post)
                try:
                    db.session.commit()
                except InternalError as e:
                    app.logger.error(e)

                    flash(f"Oh no, there was a problem posting this. We'll try to figure out the problem.")
                else:
                    flash(f"Thank you! Your post will appear soon.")

                return redirect(url_for('index'))

        else:
            for e in sform.errors.items():
                flash(e[1][0])

    return render_template('post.html.j2',
                           sform=sform,
                           app=app,
                           post=post,
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

        mastodon_host = get_or_create_host(db, app, host)

        session['mastodon'] = {
            'host': host,
            'username': creds["username"],
        }

        # first look up by account id
        user = db.session.query(User).filter_by(
                mastodon_account_id=creds["id"],
                mastodon_host_id=mastodon_host.id
        ).first()

        if not user:
            # fall back to looking up by username
            user = db.session.query(User).filter_by(
                    mastodon_user=creds["username"],
                    mastodon_host_id=mastodon_host.id
            ).first()

        if user:
            app.logger.debug("Existing settings found")
            session['user_id'] = user.id

            if user.mastodon_access_code != access_code:
                user.mastodon_access_code = access_code
                user.updated = datetime.now()
                db.session.commit()

            if user.mastodon_account_id == 0:
                user.mastodon_account_id = creds["id"]
                user.updated = datetime.now()
                db.session.commit()

        else:

            user = User()
            user.settings = Settings()
            user.mastodon_access_code = access_code
            user.mastodon_user = creds["username"]
            user.mastodon_host = mastodon_host
            user.mastodon_account_id = creds["id"]
            user.updated = datetime.now()

            db.session.add(user.settings)
            db.session.add(user)
            db.session.commit()

            session['user_id'] = user.id

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


@app.route('/delete_post/<post_id>', methods=["GET"])
def delete_post(post_id):

    post_to_delete = db.session.query(Post).filter_by(id=post_id).first()

    if not post_to_delete:
        flash("No post found")
        return redirect(url_for('index'))

    uid = session.get('user_id', None)

    if uid:
        user = db.session.query(User).filter_by(id=uid).first()

        if post_to_delete.user_id != user.id:
            flash("Permission Denied")
            return redirect(url_for('index'))

        db.session.delete(post_to_delete)
        db.session.commit()

        flash("Deleted")
    return redirect(url_for('index'))


@app.route('/logout', methods=["GET", "POST"])
def logout():
    session.pop('mastodon', None)
    session.pop('user_id', None)
    return redirect(url_for('index'))


@app.route('/privacy')
def privacy():
    return render_template('privacy.html.j2',
                           app=app)


@app.template_filter('nl2br')
def nl2br(value):
    _paragraph_re = re.compile(r'(?:\r\n|\r|\n){2,}')

    result = u'\n\n'.join(u'<p>%s</p>' % p.replace('\n', Markup('<br>\n'))
                          for p in _paragraph_re.split(escape(value)))
    return result


if __name__ == '__main__':
    app.run()
