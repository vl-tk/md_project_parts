# Generated by Django 3.2.4 on 2021-10-18 09:53

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('booking', '0035_booking_can_be_rated'),
    ]

    operations = [
        migrations.AddField(
            model_name='withdrawal',
            name='result',
            field=models.CharField(blank=True, max_length=1024, null=True),
        ),
    ]
