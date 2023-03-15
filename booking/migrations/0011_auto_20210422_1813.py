# Generated by Django 3.1.7 on 2021-04-22 18:13

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('booking', '0010_auto_20210422_1409'),
    ]

    operations = [
        migrations.AddField(
            model_name='booking',
            name='booker_fee_percent',
            field=models.PositiveIntegerField(blank=True, default=0, null=True, verbose_name='Percent used for Booker fee'),
        ),
        migrations.AddField(
            model_name='booking',
            name='dj_fee_percent',
            field=models.PositiveIntegerField(blank=True, default=0, null=True, verbose_name='Percent used for DJ fee'),
        ),
        migrations.AlterField(
            model_name='booking',
            name='booker_fee',
            field=models.FloatField(blank=True, default=0, null=True, verbose_name='Booker fee in $'),
        ),
        migrations.AlterField(
            model_name='booking',
            name='dj_fee',
            field=models.FloatField(blank=True, default=0, null=True, verbose_name='DJ fee in $'),
        ),
    ]