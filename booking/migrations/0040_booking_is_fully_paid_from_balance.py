# Generated by Django 3.2.4 on 2021-10-29 14:18

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('booking', '0039_booking_sum_to_pay_from_balance'),
    ]

    operations = [
        migrations.AddField(
            model_name='booking',
            name='is_fully_paid_from_balance',
            field=models.BooleanField(default=False),
        ),
    ]
