# Generated by Django 3.2.4 on 2021-11-15 11:22

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('booking', '0052_auto_20211115_0750'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='booking',
            name='is_fully_paid_from_balance',
        ),
        migrations.AddField(
            model_name='booking',
            name='sum_to_pay_from_card',
            field=models.FloatField(blank=True, default=0, null=True, verbose_name='Sum to pay from card in $'),
        ),
    ]
