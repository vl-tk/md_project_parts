# Generated by Django 3.2.4 on 2021-11-01 17:35

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('booking', '0041_bookingchangerecord_is_by_staff'),
    ]

    operations = [
        migrations.AddField(
            model_name='bookingchangerecord',
            name='author',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='user_booking_change_records', to=settings.AUTH_USER_MODEL),
        ),
    ]
