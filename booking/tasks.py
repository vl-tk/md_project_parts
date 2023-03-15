import logging
from datetime import timedelta

from django.db.models.aggregates import Sum
from django.db.models.expressions import Case, When
from django.db.models.fields import IntegerField
from django.utils import timezone

from booking.models import Booking
from main.celery_config import app
from notifications.service import NotifyService
from users.services.payment import PaymentService
from utils.email import (send_awaiting_acceptance_12_hours_before_to_dj,
                         send_awaiting_acceptance_24_hours_before_to_booker,
                         send_awaiting_acceptance_24_hours_before_to_dj,
                         send_awaiting_acceptance_48_hours_before_to_dj,
                         send_before_start_event_to_booker,
                         send_before_start_event_to_dj,
                         send_booking_no_dj_response_expiration,
                         send_booking_not_paid_to_booker,
                         send_booking_not_paid_to_dj,
                         send_final_reminder_to_booker_about_rating,
                         send_final_reminder_to_dj_about_rating,
                         send_reminder_to_booker_about_rating,
                         send_reminder_to_dj_about_rating)

logger = logging.getLogger('django')



@app.task(acks_late=True)
def task_for_paid_bookings():

    HOURS_BEFORE_BOOKING_START = [12, 24, 48]

    now = timezone.now()

    for booking in Booking.objects.get_paid():

        # rejection time check
        time_since_creation = now - booking.created_at
        if ((time_since_creation >= timedelta(hours=48)) or (now >= booking.get_datetime())):
            send_booking_no_dj_response_expiration(
                emails=[booking.account_booker.user.email],
                booking=booking
            )
            NotifyService().create_notify_booking_expired_no_dj_response(
                account=booking.account_booker,
                booking=booking
            )
            PaymentService().refund_money_for_booker(booking)
            booking.reject()
            continue

        # notify on hours before start
        for hours in HOURS_BEFORE_BOOKING_START:
            if (now + timedelta(hours=hours)).strftime("%Y%m%d%H") == booking.get_datetime().strftime("%Y%m%d%H"):

                # remind about not accepted booking in 12, 24, 48
                if hours == 12:
                    send_awaiting_acceptance_12_hours_before_to_dj(
                        emails=[booking.account_dj.user.email],
                        booking=booking
                    )
                    NotifyService().create_notify_awaiting_acceptance_12_hours_before(
                        account=booking.account_dj,
                        booking=booking
                    )
                if hours == 24:
                    send_awaiting_acceptance_24_hours_before_to_dj(
                        emails=[booking.account_dj.user.email],
                        booking=booking
                    )
                    send_awaiting_acceptance_24_hours_before_to_booker(
                        emails=[booking.account_dj.user.email],
                        booking=booking
                    )
                    NotifyService().create_notify_awaiting_acceptance_24_hours_before(
                        account=booking.account_dj,
                        booking=booking
                    )
                    NotifyService().create_notify_awaiting_acceptance_24_hours_before(
                        account=booking.account_booker,
                        booking=booking
                    )
                if hours == 48:
                    send_awaiting_acceptance_48_hours_before_to_dj(
                        emails=[booking.account_dj.user.email],
                        booking=booking
                    )
                    NotifyService().create_notify_awaiting_acceptance_48_hours_before(
                        account=booking.account_dj,
                        booking=booking
                    )


@app.task(acks_late=True)
def booking_delete_by_timeout_payment():
    for booking in Booking.objects.get_not_paid():

        logger.info(f'TASKDEBUG: not paid booking: {booking}')

        if booking.created_at + timedelta(minutes=10) <= timezone.now():

            logger.info(f'TASKDEBUG: sending email to booker: {booking.account_booker.user.email}')
            logger.info(f'TASKDEBUG: sending email to dj: {booking.account_dj.user.email}')

            send_booking_not_paid_to_booker(
                emails=[booking.account_booker.user.email],
                booking=booking,
            )
            send_booking_not_paid_to_dj(
                emails=[booking.account_dj.user.email],
                booking=booking,
            )
            NotifyService().create_notify_awaiting_payment(
                account=booking.account_dj,
                booking=booking
            )
            NotifyService().create_notify_awaiting_payment(
                account=booking.account_booker,
                booking=booking
            )
            PaymentService().cancel_booking_payment(booking=booking)


@app.task(acks_late=True)
def transfer_money_for_dj():
    for booking in Booking.objects.get_success():
        if timezone.now() >= (booking.get_datetime() + timedelta(days=3)):
            PaymentService().transfer_money_for_dj(booking=booking)


@app.task(acks_late=True)
def task_for_accepted_by_dj_bookings():

    HOURS_BEFORE_BOOKING_START = [12, 24, 36]

    for booking in Booking.objects.get_accepted_by_dj():

        now = timezone.now()

        if now >= booking.get_datetime():
            booking.success()

        # notify on hours before start
        for hours in HOURS_BEFORE_BOOKING_START:
            if (now + timedelta(hours=hours)).strftime("%Y%m%d%H") == booking.get_datetime().strftime("%Y%m%d%H"):
                send_before_start_event_to_booker(
                    emails=[booking.account_booker.user.email],
                    booking=booking,
                    last_hours=hours
                )
                send_before_start_event_to_dj(
                    emails=[booking.account_dj.user.email],
                    booking=booking,
                    last_hours=hours
                )
                NotifyService().create_notify_before_start_event(
                    account=booking.account_booker,
                    last_hours=hours,
                    booking=booking
                )
                NotifyService().create_notify_before_start_event(
                    account=booking.account_dj,
                    last_hours=hours,
                    booking=booking
                )


@app.task(acks_late=True)
def remind_about_booking_rating():
    HOURS_AFTER_TO_REMIND_ABOUT_RATING = [12, 96]
    now = timezone.now()

    STATUSES_TO_BE_RATED = [
        Booking.Status.SUCCESS,
        Booking.Status.COMPLETED,
        Booking.Status.IN_DISPUTE,
        Booking.Status.DISPUTED
    ]

    bookings_without_booker_ratings = Booking.objects.filter(
        status__in=STATUSES_TO_BE_RATED,
        can_be_rated=True
    ).annotate(
        booker_reviews_count=Sum(Case(
            When(reviews__is_by_booker=True, then=1),
            output_field=IntegerField(),
        ))
    ).filter(
        booker_reviews_count=None
    )

    for booking in bookings_without_booker_ratings:
        for hours in HOURS_AFTER_TO_REMIND_ABOUT_RATING:
            if (now.strftime("%Y%m%d%H") == (booking.get_datetime() + timedelta(hours=hours)).strftime("%Y%m%d%H")):
                if hours == 12:
                    send_reminder_to_booker_about_rating(
                        emails=[booking.account_booker.user.email],
                        booking=booking
                    )
                    NotifyService().create_notify_reminder_to_booker_about_rating(
                        account=booking.account_booker,
                        booking=booking
                    )
                elif hours == 96:
                    send_final_reminder_to_booker_about_rating(
                        emails=[booking.account_booker.user.email],
                        booking=booking
                    )
                    NotifyService().create_notify_reminder_to_booker_about_rating(
                        account=booking.account_booker,
                        booking=booking
                    )
                continue

    bookings_without_performer_ratings = Booking.objects.filter(
        status__in=STATUSES_TO_BE_RATED,
        can_be_rated=True
    ).annotate(
        booker_reviews_count=Sum(Case(
            When(reviews__is_by_booker=False, then=1),
            output_field=IntegerField(),
        ))
    ).filter(
        booker_reviews_count=None
    )

    for booking in bookings_without_performer_ratings:
        for hours in HOURS_AFTER_TO_REMIND_ABOUT_RATING:
            if (now.strftime("%Y%m%d%H") == (booking.get_datetime() + timedelta(hours=hours)).strftime("%Y%m%d%H")):
                if hours == 12:
                    send_reminder_to_dj_about_rating(
                        emails=[booking.account_dj.user.email],
                        booking=booking
                    )
                    NotifyService().create_notify_reminder_to_dj_about_rating(
                        account=booking.account_dj,
                        booking=booking
                    )
                elif hours == 96:
                    send_final_reminder_to_dj_about_rating(
                        emails=[booking.account_dj.user.email],
                        booking=booking
                    )
                    NotifyService().create_notify_reminder_to_dj_about_rating(
                        account=booking.account_dj,
                        booking=booking
                    )
                continue


@app.task(acks_late=True)
def disable_ratings_for_old_bookings():

    now = timezone.now()

    to_update = []
    for booking in Booking.objects.filter(can_be_rated=True):
        if (now - booking.get_datetime() >= timedelta(hours=120)):
            booking.can_be_rated = False
            to_update.append(booking)

    Booking.objects.bulk_update(to_update, ['can_be_rated'], batch_size=100)
