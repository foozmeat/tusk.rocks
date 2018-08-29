from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, RadioField, SelectField
from wtforms.fields.html5 import URLField
from wtforms.validators import DataRequired, Email, length, url


class MastodonIDForm(FlaskForm):
    mastodon_id = StringField('Enter your Mastodon ID', validators=[DataRequired(), Email()])


class SubmissionForm(FlaskForm):
    comment = TextAreaField('Message', [length(max=500)])
    share_link = URLField('Song/Album Link', [url()])
    toot_visibility = SelectField('Toot visibility', choices=[
        ('', 'Public'),
        ('private', "Private"),
        ('unlisted', 'Unlisted'),
    ])
