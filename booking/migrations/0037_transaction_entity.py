# Generated by Django 3.2.4 on 2021-10-19 14:05

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('booking', '0036_withdrawal_result'),
    ]

    operations = [
        migrations.AddField(
            model_name='transaction',
            name='entity',
            field=models.CharField(blank=True, editable=False, max_length=255, null=True, verbose_name='Object Type'),
        ),
    ]
