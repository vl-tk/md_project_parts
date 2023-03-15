# Generated by Django 3.1.7 on 2021-05-05 16:12

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('booking', '0014_transaction_withdrawal'),
    ]

    operations = [
        migrations.AlterField(
            model_name='transaction',
            name='amount',
            field=models.IntegerField(default=0, editable=False, verbose_name='Amount'),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='entity_pk',
            field=models.BigIntegerField(blank=True, editable=False, null=True, verbose_name='Object ID'),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='is_hold',
            field=models.BooleanField(blank=True, default=False, verbose_name='Is hold'),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='purpose',
            field=models.PositiveSmallIntegerField(choices=[(1, 'Money deposit to the balance'), (2, 'Payment by booker for Booking'), (3, 'Payment to performer for Booking'), (4, 'Refund of money to the balance'), (5, 'Withdrawal of money from system')], editable=False, verbose_name='Purpose'),
        ),
        migrations.AlterField(
            model_name='withdrawal',
            name='amount',
            field=models.FloatField(default=0, verbose_name='Amount'),
        ),
    ]