import argparse
import importlib
import logging
import mimetypes
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import psutil
import requests
from mastodon import Mastodon, MastodonAPIError, MastodonNetworkError
from sqlalchemy import create_engine, exc, func
from sqlalchemy.orm import Session

from tr.models import Post

start_time = time.time()

config = os.environ.get('TR_CONFIG', 'DevelopmentConfig')
c = getattr(importlib.import_module('config'), config)

if c.SENTRY_DSN:
    from raven import Client

    client = Client(c.SENTRY_DSN)

parser = argparse.ArgumentParser(description='Worker')
parser.add_argument('--worker', dest='worker', type=int, required=False, default=1)
args = parser.parse_args()

# worker_stat = WorkerStat(worker=args.worker)

FORMAT = "%(asctime)-15s [%(filename)s:%(lineno)s : %(funcName)s()] %(message)s"

logging.basicConfig(format=FORMAT)

l = logging.getLogger('worker')

if c.DEBUG:
    l.setLevel(logging.DEBUG)
else:
    l.setLevel(logging.INFO)


def check_worker_stop():
    if Path('worker_stop').exists():
        l.info("Worker paused...exiting")
        # session.add(worker_stat)
        session.commit()
        session.close()
        exit(0)


# logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

l.info("Starting up…")
engine = create_engine(c.SQLALCHEMY_DATABASE_URI)
engine.connect()

try:
    engine.execute('SELECT 1 from users')
except exc.SQLAlchemyError as e:
    l.error(e)
    sys.exit()

session = Session(engine)

check_worker_stop()

lockfile = Path(f'worker_{args.worker}.lock')

if Path(lockfile).exists():
    l.info("Worker lock found")
    with lockfile.open() as f:
        pid = f.readline()
        try:
            pid = int(pid)
        except ValueError:
            l.info("Corrupt lock file found")
            lockfile.unlink()

        else:
            if pid in psutil.pids():
                l.info("Worker process still running...exiting")
                session.commit()
                session.close()
                exit(0)
            else:
                l.info("Stale Worker found")

with lockfile.open('wt') as f:
    f.write(str(psutil.Process().pid))

posts = session.query(Post).filter_by(posted=False)
s = requests.Session()

# if not c.DEVELOPMENT:
#     posts = posts.order_by(func.rand())

for post in posts:

    user = post.user
    mastodonhost = user.mastodon_host

    if mastodonhost.defer_until and mastodonhost.defer_until > datetime.now():
        l.warning(f"Deferring connections to {mastodonhost.hostname}")
        continue

    media_ids = []

    mast_api = Mastodon(
            client_id=mastodonhost.client_id,
            client_secret=mastodonhost.client_secret,
            api_base_url=f"https://{mastodonhost.hostname}",
            access_token=user.mastodon_access_code,
            debug_requests=False,
            request_timeout=10
    )

    l.info(f"{user.mastodon_user}")

    if c.SEND and post.album_art:
        l.info(f"Downloading {post.album_art}")
        attachment_file = requests.get(post.album_art, stream=True)
        attachment_file.raw.decode_content = True
        temp_file = tempfile.NamedTemporaryFile(delete=False)
        temp_file.write(attachment_file.raw.read())
        temp_file.close()

        path = urlparse(post.album_art).path
        file_extension = mimetypes.guess_extension(attachment_file.headers['Content-type'])

        # ffs
        if file_extension == '.jpe':
            file_extension = '.jpg'

        upload_file_name = temp_file.name + file_extension
        os.rename(temp_file.name, upload_file_name)
        l.debug(f'Uploading {upload_file_name}')

        try:
            media_ids.append(mast_api.media_post(upload_file_name))
        except MastodonAPIError as e:
            l.error(e)
            continue

        except MastodonNetworkError as e:
            l.error(e)
            mastodonhost.defer()
            session.commit()
            continue

    message_to_post = f"{post.comment}\n\n{post.share_link}"

    vis = 'public'
    if post.toot_visibility:
        vis = post.toot_visibility

    l.info(message_to_post)

    if c.SEND:
        try:
            new_message = mast_api.status_post(
                    message_to_post,
                    visibility=vis,
                    media_ids=media_ids)

        except MastodonAPIError as e:
            l.error(e)
            continue

        except MastodonNetworkError as e:
            l.error(e)
            mastodonhost.defer()
            session.commit()
            continue

        else:
            post.updated = datetime.now()
            post.status_id = new_message["id"]
            post.posted = True
            session.commit()

            if c.ACCOUNT_ACCESS_TOKEN:
                tusk_poster_api = Mastodon(
                        client_id=c.ACCOUNT_CLIENT_ID,
                        client_secret=c.ACCOUNT_CLIENT_SECRET,
                        api_base_url=c.ACCOUNT_BASE_URL,
                        access_token=c.ACCOUNT_ACCESS_TOKEN,
                        debug_requests=False,
                        request_timeout=10
                )

                try:
                    tusk_poster_api.status_reblog(new_message)
                except MastodonAPIError as e:
                    l.error(e)

    check_worker_stop()

l.info(f"-- All done")

lockfile.unlink()
