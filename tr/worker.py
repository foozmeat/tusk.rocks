import argparse
import importlib
import logging
import os
import smtplib
import sys
import time
import requests
from datetime import datetime
from pathlib import Path
from typing import Any, List

from mastodon import Mastodon, MastodonAPIError, MastodonNetworkError
from requests import Request
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

if Path('worker_stop').exists():
    l.info("Worker paused...exiting")
    # worker_stat.time = 0
    # session.add(worker_stat)
    # session.commit()
    session.close()
    exit(0)

posts = session.query(Post).filter_by(posted=False)
s = requests.Session()

if not c.DEVELOPMENT:
    posts = posts.order_by(func.rand())

for post in posts:

    user = post.user
    mastodonhost = user.mastodon_host

    mast_api = Mastodon(
            client_id=mastodonhost.client_id,
            client_secret=mastodonhost.client_secret,
            api_base_url=f"https://{mastodonhost.hostname}",
            access_token=user.mastodon_access_code,
            debug_requests=False,
            request_timeout=10
    )

    l.info(f"{user.mastodon_user}")

    l.info(f"Checking {post.song_link}")

    req = Request('GET', post.song_link, headers={'User-Agent': 'curl/7.54.0'})
    prepped = req.prepare()
    r = s.send(prepped)

    if r.status_code == 404:
        song_link = post.share_link
    else:
        song_link = r.url

    message_to_post = f"{post.comment}\n\n{song_link}"

    try:
        new_message = mast_api.status_post(
                message_to_post,
                visibility='public')

        l.info(new_message)
        post_success = True

    except MastodonAPIError as e:
        l.error(e)
        continue

    except MastodonNetworkError:
        l.error(e)
        continue

    post.post_link = new_message['url']
    post.posted = True
    session.commit()

