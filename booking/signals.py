import datetime

from django.db.models.signals import post_save
from django.dispatch import receiver

from booking.models import Booking, BookingChangeRecord


@receiver(post_save, sender=Booking)
def booking_changed(sender, instance, created, **kwargs):

    booking = instance

    # update dj_profile future_busy_dates

    dj_profile = booking.account_dj.dj_profile

    if dj_profile.future_busy_dates is None:
        dj_profile.future_busy_dates = []

    dates = dj_profile.future_busy_dates + [d for d in booking.dj_busy_dates]

    dj_profile.future_busy_dates = sorted(list(set([d for d in dates if d >= datetime.datetime.now().date()])))
    dj_profile.save()

    # track changes

    for field_name in Booking.TRACKABLE_FIELDS:
        if booking.tracker.has_changed(field_name):

            is_by_staff = getattr(booking, 'is_by_staff', False)
            author = getattr(booking, 'change_author', None)

            BookingChangeRecord.objects.create(
                booking=booking,
                field_name=field_name,
                value_before=booking.tracker.previous(field_name),
                value_after=getattr(booking, field_name),
                is_by_staff=is_by_staff,
                author=author
            )
