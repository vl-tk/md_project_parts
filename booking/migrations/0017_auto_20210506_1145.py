# Generated by Django 3.1.7 on 2021-05-06 11:45

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('booking', '0016_booking_stripe_fee'),
    ]

    operations = [
        migrations.AlterField(
            model_name='transaction',
            name='purpose',
            field=models.PositiveSmallIntegerField(choices=[(1, 'Money deposit to the balance'), (2, 'Escrow for Booking'), (3, 'Fee for Booking'), (4, 'Payment to performer for Booking'), (5, 'Fee for performer'), (6, 'Refund of money to the balance'), (7, 'Withdrawal of money from system')], editable=False, verbose_name='Purpose'),
        ),
    ]
