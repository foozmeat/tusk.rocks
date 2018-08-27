from flask import url_for
from mastodon import Mastodon, MastodonNetworkError

from sr.models import MastodonHost


def get_or_create_host(db, app, hostname):
    mastodonhost = db.session.query(MastodonHost).filter_by(hostname=hostname).first()

    if not mastodonhost:

        try:
            client_id, client_secret = Mastodon.create_app(
                    "SonicReducer",
                    scopes=["read", "write"],
                    api_base_url=f"https://{hostname}",
                    website="https://song.delivery/",
                    redirect_uris=url_for("mastodon_oauthorized", _external=True)
            )

            app.logger.info(f"New host created for {hostname}")

            mastodonhost = MastodonHost(hostname=hostname,
                                        client_id=client_id,
                                        client_secret=client_secret)
            db.session.add(mastodonhost)
            db.session.commit()
        except MastodonNetworkError as e:
            app.logger.error(e)
            return None

    app.logger.debug(f"Using Mastodon Host: {mastodonhost.hostname}")

    return mastodonhost


def mastodon_api(db, app, hostname, access_code=None):
    mastodonhost = get_or_create_host(db, app, hostname)

    if mastodonhost:
        api = Mastodon(
                client_id=mastodonhost.client_id,
                client_secret=mastodonhost.client_secret,
                api_base_url=f"https://{mastodonhost.hostname}",
                access_token=access_code,
                debug_requests=False
        )

        return api
    return None


