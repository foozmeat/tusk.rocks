from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, RadioField
from wtforms.fields.html5 import URLField
from wtforms.validators import DataRequired, Email, length, url


class MastodonIDForm(FlaskForm):
    mastodon_id = StringField('Enter your Mastodon ID', validators=[DataRequired(), Email()])


class SubmissionForm(FlaskForm):
    comment = TextAreaField('Comment', [length(max=500)])
    share_link = URLField('Link: ', [url()])
    toot_visibility = RadioField('Toot visibility', choices=[
        ('public', 'Public'),
        ('private', "Private"),
        ('unlisted', 'Unlisted'),
    ])
