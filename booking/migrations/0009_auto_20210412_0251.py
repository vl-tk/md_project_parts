# Generated by Django 3.1.7 on 2021-04-12 02:51

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('booking', '0008_auto_20210411_2304'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='booking',
            name='is_payed',
        ),
        migrations.AlterField(
            model_name='booking',
            name='status',
            field=models.IntegerField(choices=[(1, 'Not paid'), (2, 'Paid'), (3, 'Declined by booker'), (4, 'Declined by DJ'), (5, 'Success'), (6, 'Completed')], default=1, verbose_name='status'),
        ),
    ]
