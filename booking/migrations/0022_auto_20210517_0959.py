# Generated by Django 3.1.7 on 2021-05-17 09:59

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('booking', '0021_merge_20210514_1321'),
    ]

    operations = [
        migrations.AlterField(
            model_name='booking',
            name='duration',
            field=models.PositiveIntegerField(verbose_name='gig duration, in minutes'),
        ),
        migrations.AlterField(
            model_name='booking',
            name='status',
            field=models.IntegerField(choices=[(1, 'Not paid'), (2, 'Paid'), (3, 'Declined by booker'), (4, 'Declined by DJ'), (5, 'Success'), (6, 'Completed'), (7, 'Accepted by DJ'), (8, 'Canceled by booker'), (9, 'Canceled by DJ'), (10, 'Rejected'), (11, 'Declined by stuff')], default=1, verbose_name='status'),
        ),
    ]
