import os
import random
from datetime import date, datetime, timedelta
from unittest.mock import patch

from django.conf import settings
from django.core import mail
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.timezone import make_aware

from rest_framework import status
from rest_framework.reverse import reverse

from booking.enums import PromocodeType, TransactionPurposes
from booking.models import Booking, BookingReview, Promocode, Transaction
from booking.tasks import (booking_delete_by_timeout_payment,
                           disable_ratings_for_old_bookings,
                           remind_about_booking_rating,
                           task_for_accepted_by_dj_bookings,
                           task_for_paid_bookings)
from users.enums import AccountTypes
from users.models import DJProfile
from users.services.payment import PaymentService
from utils.email import (SEND_BEFORE_START_EVENT_TO_BOOKER,
                         SEND_REMINDER_TO_BOOKER_ABOUT_RATING)
from utils.test import AuthClientTestCase

DECLINE_COMMENT = """\
Contrary to popular belief, Lorem Ipsum is not simply random text. \
It has roots in a piece of classical Latin literature from 45 BC, \
making it over 2000 years old. Richard McClintock, a Latin professor \
at Hampden-Sydney College in Virginia, looked up one of the more obscure \
Latin words, consectetur. Thanks!"""


class BookingTestCase(AuthClientTestCase):

    def __create_booking_response(self, data: dict):
        return self.booker_client.post(reverse('booking:booking_list_create'), data=data)

    @staticmethod
    def __confirm_payment_intent(payment_intent: str):
        PaymentService().provider.PaymentIntent.confirm(payment_intent, payment_method='pm_card_visa')

    def test_create_by_dj(self):
        with patch.object(timezone, 'now', return_value=make_aware(datetime(2021, 3, 1, 0, 0))):
            data = {
                'account_dj': self.dj_user.get_account().uuid,
                'comment': 'Comment',
                'date': '2021-03-13',
                'time': '18:00',
                'duration': 1200,
                'gig_type': 1,
                'listeners': 2,
                'out_door_play': 1,
                'in_door_play': 1,
                'music_list': [1]
            }
            response = self.dj_client.post(reverse('booking:booking_list_create'), data=data)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertEqual(response.data['comment'], data['comment'])
            self.assertIsNotNone(response.data['payment_intent_client_secret'])

    def test_create_by_booker(self):
        with patch.object(timezone, 'now', return_value=make_aware(datetime(2021, 3, 1, 0, 0))):
            data = {
                'account_dj': self.dj_user.get_account().uuid,
                'comment': 'Comment',
                'date': '2021-03-13',
                'time': '18:00',
                'duration': 1200,
                'gig_type': 1,
                'listeners': 2,
                'out_door_play': 1,
                'in_door_play': 1,
                'music_list': [1]
            }
            response = self.__create_booking_response(data=data)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertEqual(response.data['comment'], data['comment'])
            self.assertIsNotNone(response.data['payment_intent_client_secret'])

            response = self.__create_booking_response(data=data)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(str(response.data['date'][0]), 'Date(s) 2021-03-13,2021-03-14 already taken')

    def test_booking_costs_on_create(self):
        with patch.object(timezone, 'now', return_value=make_aware(datetime(2021, 3, 10, 0, 0))):
            data = {
                'account_dj': self.dj_user.get_account().uuid,
                'comment': 'Comment',
                'date': '2021-03-13',
                'time': '18:00',
                'duration': 1200,
                'gig_type': 1,
                'listeners': 2,
                'out_door_play': 1,
                'in_door_play': 1,
                'music_list': [1]
            }
            response = self.__create_booking_response(data=data)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            booking = Booking.objects.get(id=response.data['id'])
            self.assertEqual(response.data['performer_cost'], round(booking.duration / 60 * booking.price_per_hour, 2))
            self.assertEqual(response.data['setup_time_cost'], booking.price_per_hour)
            self.assertEqual(response.data['performance_cost'], booking.get_price() - booking.booker_fee)
            self.assertEqual(response.data['total_cost'], booking.get_price())

    def test_create_with_invalid_price(self):
        """default price will be used"""

        with patch.object(timezone, 'now', return_value=make_aware(datetime(2021, 3, 13, 0, 0))):
            data = {
                'account_dj': self.dj_user.get_account().uuid,
                'comment': 'Comment',
                'date': '2021-03-13',
                'time': '18:00',
                'duration': 180,
                'gig_type': 1,
                'listeners': 2,
                'out_door_play': 1,
                'in_door_play': 1,
                'music_list': [1]
            }
            response = self.__create_booking_response(data=data)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_booking_paid_2step_checkout(self):
        """payment without user balance"""

        with patch.object(timezone, 'now', return_value=make_aware(datetime(2021, 3, 13, 0, 0))):

            # booking
            data = {
                'account_dj': self.dj_user.get_account().uuid,
                'comment': 'Comment',
                'date': '2021-03-13',
                'time': '18:00',
                'duration': 180,
                'gig_type': 1,
                'listeners': 2,
                'out_door_play': 1,
                'in_door_play': 1,
                'music_list': [1]
            }
            response = self.__create_booking_response(data=data)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertEqual(response.data['payment_intent_id'], '')
            self.assertEqual(response.data['total_cost'], 350)
            self.assertEqual(response.data['performance_cost'], 332.5)  # total_cost - booker_fee
            self.assertEqual(response.data['performer_cost'], 30.0)  # 10 * 3 hours
            self.assertEqual(response.data['price_per_hour'], 10.0)
            self.assertEqual(response.data['dj_fee'], 35.0)
            self.assertEqual(response.data['booker_fee'], 17.5)
            self.assertEqual(response.data['sum_to_pay_from_balance'], 0)
            self.assertEqual(response.data['sum_to_pay_from_card'], 350.0)

            # checkout page

            response = self.booker_client.post(
                reverse('booking:booking_pay', args=(response.data['id'],)),
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertIn('pi_', response.data['payment_intent_id'])
            self.assertIn('secret', response.data['payment_intent_client_secret'])
            self.assertEqual(response.data['status'], Booking.Status.NOT_PAID)

            # step similar to action on the frontend
            self.__confirm_payment_intent(
                payment_intent=response.data['payment_intent_id']
            )

            # after stripe webhook is called
            booking = Booking.objects.get(pk=response.data['id'])
            PaymentService().pay_booking(booking.payment_intent_id, amount=int(booking.get_price() * 100))

            booking = Booking.objects.get(pk=response.data['id'])
            self.assertEqual(booking.status, Booking.Status.PAID)

            self.assertEqual(Transaction.objects.count(), 3)

            transactions = Transaction.objects.filter(entity_pk=booking.pk).order_by('id')

            self.assertEqual(transactions[0].purpose, TransactionPurposes.PAYMENT)
            self.assertEqual(transactions[0].amount, int(booking.get_price() * 100))

            self.assertEqual(transactions[1].purpose, TransactionPurposes.BOOKING_ESCROW)
            booking_escrow_value = -1 * (PaymentService._convert_amount_to_cents(booking.get_price()) - PaymentService._convert_amount_to_cents(booking.booker_fee))
            self.assertEqual(transactions[1].amount, booking_escrow_value)

            self.assertEqual(transactions[2].purpose, TransactionPurposes.BOOKING_FEE_FOR_BOOKER)
            self.assertEqual(transactions[2].amount, int(-1 * booking.booker_fee * 100))

    def test_create_booking_paid_2step_checkout_stripe_and_user_balance(self):

        with patch.object(timezone, 'now', return_value=make_aware(datetime(2021, 3, 13, 0, 0))):

            # user balance
            self.test_data_service.create_random_transaction(
                purpose=TransactionPurposes.PAYMENT,
                amount=10000,
                user=self.booker_user
            )

            # booking
            data = {
                'account_dj': self.dj_user.get_account().uuid,
                'comment': 'Comment',
                'date': '2021-03-13',
                'time': '18:00',
                'duration': 180,
                'gig_type': 1,
                'listeners': 2,
                'out_door_play': 1,
                'in_door_play': 1,
                'music_list': [1]
            }
            response = self.__create_booking_response(data=data)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertEqual(response.data['payment_intent_id'], '')
            self.assertEqual(response.data['total_cost'], 350)
            self.assertEqual(response.data['performance_cost'], 332.5)  # total_cost - booker_fee
            self.assertEqual(response.data['performer_cost'], 30.0)  # 10 * 3 hours
            self.assertEqual(response.data['price_per_hour'], 10.0)
            self.assertEqual(response.data['dj_fee'], 35.0)
            self.assertEqual(response.data['booker_fee'], 17.5)
            self.assertEqual(response.data['sum_to_pay_from_balance'], 100.0)
            self.assertEqual(response.data['sum_to_pay_from_card'], 250.0)

            # checkout page

            response = self.booker_client.post(
                reverse('booking:booking_pay', args=(response.data['id'],)),
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertIn('pi_', response.data['payment_intent_id'])
            self.assertIn('secret', response.data['payment_intent_client_secret'])
            self.assertEqual(response.data['status'], Booking.Status.NOT_PAID)

            # step similar to action on the frontend
            self.__confirm_payment_intent(
                payment_intent=response.data['payment_intent_id']
            )

            # # after stripe webhook is called
            booking = Booking.objects.get(pk=response.data['id'])

            sum_to_pay_from_card = PaymentService()._convert_amount_to_cents(
                booking.get_price() - booking.sum_to_pay_from_balance
            )
            PaymentService().pay_booking(
                booking.payment_intent_id,
                amount=sum_to_pay_from_card # TODO: possibly improve
            )

            booking = Booking.objects.get(pk=response.data['id'])
            self.assertEqual(booking.status, Booking.Status.PAID)

            self.assertEqual(Transaction.objects.count(), 4)

            transactions = Transaction.objects.filter(user=self.booker_user).order_by('id')

            self.assertEqual(transactions[0].purpose, TransactionPurposes.PAYMENT)
            self.assertEqual(transactions[0].amount, int(booking.sum_to_pay_from_balance * 100))

            self.assertEqual(transactions[1].purpose, TransactionPurposes.PAYMENT)
            self.assertEqual(transactions[1].amount, int(sum_to_pay_from_card))

            self.assertEqual(transactions[2].purpose, TransactionPurposes.BOOKING_ESCROW)
            booking_escrow_value = -1 * (PaymentService._convert_amount_to_cents(booking.get_price()) - PaymentService._convert_amount_to_cents(booking.booker_fee))
            self.assertEqual(transactions[2].amount, booking_escrow_value)

            self.assertEqual(transactions[3].purpose, TransactionPurposes.BOOKING_FEE_FOR_BOOKER)
            self.assertEqual(transactions[3].amount, int(-1 * booking.booker_fee * 100))

    def test_create_booking_paid_2step_checkout_only_user_balance(self):

        with patch.object(timezone, 'now', return_value=make_aware(datetime(2021, 3, 13, 0, 0))):

            # user balance
            self.test_data_service.create_random_transaction(
                purpose=TransactionPurposes.PAYMENT,
                amount=35_000,
                user=self.booker_user
            )

            # booking
            data = {
                'account_dj': self.dj_user.get_account().uuid,
                'comment': 'Comment',
                'date': '2021-03-13',
                'time': '18:00',
                'duration': 180,
                'gig_type': 1,
                'listeners': 2,
                'out_door_play': 1,
                'in_door_play': 1,
                'music_list': [1]
            }
            response = self.__create_booking_response(data=data)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertEqual(response.data['payment_intent_id'], '')
            self.assertEqual(response.data['total_cost'], 350)
            self.assertEqual(response.data['performance_cost'], 332.5)  # total_cost - booker_fee
            self.assertEqual(response.data['performer_cost'], 30.0)  # 10 * 3 hours
            self.assertEqual(response.data['price_per_hour'], 10.0)
            self.assertEqual(response.data['dj_fee'], 35.0)
            self.assertEqual(response.data['booker_fee'], 17.5)
            self.assertEqual(response.data['sum_to_pay_from_balance'], 350.0)
            self.assertEqual(response.data['sum_to_pay_from_card'], 0)

            # checkout page

            response = self.booker_client.post(
                reverse('booking:booking_pay', args=(response.data['id'],)),
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data['payment_intent_id'], '')
            self.assertEqual(response.data['payment_intent_client_secret'], '')
            self.assertEqual(response.data['status'], Booking.Status.PAID)

            # step similar to action on the frontend
            # (nothing is done on the frontend)

            booking = Booking.objects.get(pk=response.data['id'])
            self.assertEqual(booking.status, Booking.Status.PAID)

            self.assertEqual(Transaction.objects.count(), 3)

            transactions = Transaction.objects.filter(user=self.booker_user).order_by('id')

            self.assertEqual(transactions[0].purpose, TransactionPurposes.PAYMENT)
            self.assertEqual(transactions[0].amount, 35_000)

            self.assertEqual(transactions[1].purpose, TransactionPurposes.BOOKING_ESCROW)
            booking_escrow_value = -1 * (PaymentService._convert_amount_to_cents(booking.get_price()) - PaymentService._convert_amount_to_cents(booking.booker_fee))
            self.assertEqual(transactions[1].amount, booking_escrow_value)

            self.assertEqual(transactions[2].purpose, TransactionPurposes.BOOKING_FEE_FOR_BOOKER)
            self.assertEqual(transactions[2].amount, int(-1 * booking.booker_fee * 100))

    def test_create_booking_paid_2step_checkout_booking_already_paid(self):

        booking = self.test_data_service.create_custom_booking(
            account_booker=self.booker_user.get_accounts().first(),
            account_dj=self.dj_user.get_accounts().first(),
            status=Booking.Status.PAID
        )

        response = self.booker_client.post(
            reverse('booking:booking_pay', args=(booking.pk,)),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(str(response.data['booking'][0]), 'Booking is already paid')

    def test_create_booking_paid_2step_checkout_incorrect_user(self):

        booking = self.test_data_service.create_custom_booking(
            account_booker=self.booker_user.get_accounts().first(),
            account_dj=self.dj_user.get_accounts().first(),
            status=Booking.Status.NOT_PAID
        )

        response = self.dj_client.post(
            reverse('booking:booking_pay', args=(booking.pk,)),
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_by_booker_with_event_address(self):
        """initial chat messages will be created in the booking chat room."""

        with patch.object(timezone, 'now', return_value=make_aware(datetime(2021, 3, 13, 0, 0))):

            data = {
                'account_dj': self.dj_user.get_account().uuid,
                'comment': 'Comment',
                'date': '2021-03-13',
                'time': '18:00',
                'duration': 1200,
                'gig_type': 1,
                'listeners': 2,
                'out_door_play': 1,
                'in_door_play': 1,
                'music_list': [1],
                'event_address': '123456 Test Address'
            }
            response = self.__create_booking_response(data=data)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)

            response = self.booker_client.post(
                reverse('booking:booking_pay', args=(response.data['id'],)),
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertIn('pi_', response.data['payment_intent_id'])
            self.assertIn('secret', response.data['payment_intent_client_secret'])
            self.assertEqual(response.data['status'], Booking.Status.NOT_PAID)

            # step similar to action on the frontend
            booking = Booking.objects.get(pk=response.data['id'])
            self.__confirm_payment_intent(payment_intent=booking.payment_intent_id)

            # after stripe webhook is called
            PaymentService().pay_booking(booking.payment_intent_id, amount=int(booking.get_price() * 100))

            booking = Booking.objects.get(pk=response.data['id'])
            self.assertEqual(booking.room.messages.count(), 1)

    def test_booking_busy_days_calculation(self):
        with patch.object(timezone, 'now', return_value=make_aware(datetime(2021, 5, 1, 0, 0))):
            bookings_params = [
                (
                    {
                        'account_dj': self.dj_user.get_account().uuid,
                        'comment': 'Comment',
                        'date': '2021-05-01',
                        'time': '23:00',
                        'duration': 59,
                        'gig_type': 1,
                        'listeners': 2,
                        'out_door_play': 1,
                        'in_door_play': 1,
                        'music_list': [1]
                    },
                    [date(2021, 5, 1)]
                ),
                (
                    {
                        'account_dj': self.dj_user.get_account().uuid,
                        'comment': 'Comment',
                        'date': '2021-05-06',
                        'time': '23:00',
                        'duration': 120,
                        'gig_type': 1,
                        'listeners': 2,
                        'out_door_play': 1,
                        'in_door_play': 1,
                        'music_list': [1]
                    },
                    [date(2021, 5, 6), date(2021, 5, 7)]
                ),
                (
                    {
                        'account_dj': self.dj_user.get_account().uuid,
                        'comment': 'Comment',
                        'date': '2021-05-3',
                        'time': '23:00',
                        'duration': 1560,
                        'gig_type': 1,
                        'listeners': 2,
                        'out_door_play': 1,
                        'in_door_play': 1,
                        'music_list': [1]
                    },
                    [date(2021, 5, 3), date(2021, 5, 4), date(2021, 5, 5)]
                )
            ]
            for booking in bookings_params:
                response = self.__create_booking_response(data=booking[0])
                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
                self.assertListEqual(response.data['dj_busy_dates'], [v.strftime('%Y-%m-%d') for v in booking[1]])

    def test_booking_create_on_busy_days(self):
        with patch.object(timezone, 'now', return_value=make_aware(datetime(2021, 5, 1, 0, 0))):
            data = {
                'account_dj': self.dj_user.get_account().uuid,
                'comment': 'Comment',
                'date': '2021-05-06',
                'time': '23:00',
                'duration': 120,
                'gig_type': 1,
                'listeners': 2,
                'out_door_play': 1,
                'in_door_play': 1,
                'music_list': [1]
            }
            response = self.__create_booking_response(data=data)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            busy_dates = [date(2021, 5, 6), date(2021, 5, 7)]
            self.assertListEqual(response.data['dj_busy_dates'], [
                                v.strftime('%Y-%m-%d') for v in busy_dates])

            data = {
                'account_dj': self.dj_user.get_account().uuid,
                'comment': 'Comment',
                'date': '2021-05-08',
                'time': '23:00',
                'duration': 120,
                'gig_type': 1,
                'listeners': 2,
                'out_door_play': 1,
                'in_door_play': 1,
                'music_list': [1]
            }
            response = self.__create_booking_response(data=data)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            busy_dates = [date(2021, 5, 8), date(2021, 5, 9)]
            self.assertListEqual(response.data['dj_busy_dates'], [v.strftime('%Y-%m-%d') for v in busy_dates])

            data = {
                'account_dj': self.dj_user.get_account().uuid,
                'comment': 'Comment',
                'date': '2021-05-07',
                'time': '12:00',
                'duration': 1440,
                'gig_type': 1,
                'listeners': 2,
                'out_door_play': 1,
                'in_door_play': 1,
                'music_list': [1]
            }
            response = self.__create_booking_response(data=data)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(str(response.data['date'][0]), 'Date(s) 2021-05-07,2021-05-08 already taken')

    def test_booking_create_in_less_than_2_hours(self):
        value = make_aware(datetime(2021, 5, 7, 10, 00))
        with patch.object(timezone, 'now', return_value=value) as mock_now:
            data = {
                'account_dj': self.dj_user.get_account().uuid,
                'comment': 'Comment',
                'date': '2021-05-07',
                'time': '12:00',
                'duration': 1440,
                'gig_type': 1,
                'listeners': 2,
                'out_door_play': 1,
                'in_door_play': 1,
                'music_list': [1]
            }
            response = self.__create_booking_response(data=data)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(str(response.data['date'][0]), 'Date and time of the event should be more than 2 hours from now')

    def test_get_busy_dates_for_dj_incorrect_account(self):
        with self.assertRaisesMessage(ValidationError, "DJ Account expected"):
            Booking.objects.get_busy_dates_for_dj(
                self.booker_user.get_account()
            )

    def test_user_profile_account_type(self):
        with patch.object(timezone, 'now', return_value=make_aware(datetime(2021, 3, 13, 0, 0))):
            data = {
                'account_dj': self.dj_user.get_account().uuid,
                'comment': 'Comment',
                'date': '2021-03-13',
                'time': '18:00',
                'duration': 1200,
                'gig_type': 1,
                'listeners': 2,
                'out_door_play': 1,
                'in_door_play': 1,
                'music_list': [1]
            }
            response = self.__create_booking_response(data=data)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertEqual(response.data['booker_profile']['account_type'], AccountTypes.BOOKER.value)

            data['date'] = '2021-03-16'
            response = self.dj_and_booker_client.post(reverse('booking:booking_list_create'), data=data)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertEqual(response.data['booker_profile']['account_type'], AccountTypes.DJ.value)

    def test_create_booking_report_file(self):
        with patch.object(timezone, 'now', return_value=make_aware(datetime(2021, 3, 1, 0, 0))):
            data = {
                'location_state': 'Mexico',
                'location_city': 'New York',
                'account_dj': self.dj_user.get_account().uuid,
                'comment': 'Some comment',
                'date': '2021-03-13',
                'time': '18:00',
                'duration': 1200,
                'gig_type': 1,
                'listeners': 2,
                'out_door_play': 1,
                'in_door_play': 1,
                'music_list': [1, 2, 3, 4],
                'clean_mix': True,
                'virtual_mix': True,
                'add_ons_microphone': True,
                'add_ons_fog_machine': True,
                'add_ons_power_cords': True,
                'add_ons_speakers': True
            }
            response = self.__create_booking_response(data=data)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            booking = Booking.objects.get(pk=response.data['id'])
            response = self.booker_client.post(reverse('booking:create_report_file', args=[booking.pk]))
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            file_path = os.path.join(settings.FILE_WORKER_MEDIA_ROOT, response.data['file_name'])
            self.assertTrue(os.path.exists(file_path))


class BookingPromocodeTestCase(AuthClientTestCase):

    def __create_booking_response(self, data: dict):
        return self.booker_client.post(reverse('booking:booking_list_create'), data=data)

    @staticmethod
    def __confirm_payment_intent(payment_intent: str):
        PaymentService().provider.PaymentIntent.confirm(payment_intent, payment_method='pm_card_visa')

    # PROMOCODES

    def test_booking_promocode_application_errors(self):

        booking = self.test_data_service.create_custom_booking(
            account_booker=self.booker_user.get_account(),
            account_dj=self.dj_user.get_account()
        )

        pr = self.test_data_service.create_random_promocode()
        PROMOCODE = pr.code
        self.assertEqual(pr.bookings.count(), 0)

        response = self.booker_client.post(
            reverse('booking:booking_promocode_application', args=(booking.pk,)),
            data={'promocode': PROMOCODE}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(pr.bookings.count(), 1)

        # double application

        response = self.booker_client.post(
            reverse('booking:booking_promocode_application', args=(booking.pk,)),
            data={'promocode': PROMOCODE}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(str(response.data['promocode'][0]), 'Promocode is already used for this booking')

        # incorrect promocode

        response = self.booker_client.post(
            reverse('booking:booking_promocode_application', args=(booking.pk,)),
            data={'promocode': 'ABCDEF_NO_SUCH_CODE'}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(str(response.data['promocode'][0]), 'Promocode does not exist or is not valid')

        # incorrect booking

        response = self.booker_client.post(
            reverse('booking:booking_promocode_application', args=(12345678,)),
            data={'promocode': PROMOCODE}
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # start_date, end_date limits

        pr2 = self.test_data_service.create_random_promocode(
            start_date=datetime.now() + timedelta(days=1)
        )

        response = self.booker_client.post(
            reverse('booking:booking_promocode_application', args=(booking.pk,)),
            data={'promocode': pr2.code}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            'Promocode can\'t be used as it\'s not active before',
            str(response.data['promocode'][0])
        )

        pr3 = self.test_data_service.create_random_promocode(
            start_date=datetime.now() - timedelta(days=10),
            end_date=datetime.now() - timedelta(days=5)
        )

        response = self.booker_client.post(
            reverse('booking:booking_promocode_application', args=(booking.pk,)),
            data={'promocode': pr3.code}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            'Promocode can\'t be used as it\'s not active after',
            str(response.data['promocode'][0])
        )

        # max_application_count exceeded

        pr4 = self.test_data_service.create_random_promocode(
            max_application_count=1
        )
        self.assertEqual(pr4.bookings.count(), 0)

        response = self.booker_client.post(
            reverse('booking:booking_promocode_application', args=(booking.pk,)),
            data={'promocode': pr4.code}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(pr.bookings.count(), 1)

        booking2 = self.test_data_service.create_custom_booking(
            account_booker=self.booker_user.get_account(),
            account_dj=self.dj_user.get_account()
        )

        response = self.booker_client.post(
            reverse('booking:booking_promocode_application', args=(booking2.pk,)),
            data={'promocode': pr4.code}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
           str(response.data['promocode'][0]),
           'Promocode has been used too many times.'
        )

    def test_booking_multi_promocode_application_price_calculation(self):

        with patch.object(timezone, 'now', return_value=make_aware(datetime(2021, 3, 13, 0, 0))):

            booking = self.test_data_service.create_custom_booking(
                account_booker=self.booker_user.get_account(),
                account_dj=self.dj_user.get_account()
            )

            # 1st promocode

            pr1 = self.test_data_service.create_random_promocode(
                promocode_type=PromocodeType.FIXED.value,
                amount=10
            )

            response = self.booker_client.post(
                reverse('booking:booking_promocode_application', args=(booking.pk,)),
                data={'promocode': pr1.code}
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

            booking = Booking.objects.get(pk=booking.pk)

            self.assertEqual(len(booking.applied_discounts.keys()), 1)

            (k, v), = booking.applied_discounts.items()

            self.assertEqual(v, {
                'amount': 10.0,
                'calculated_amount': 10.0,
                'date': '13-Mar-2021 (00:00:00.000000)',
                'price': 350.0,
                'promocode_type': 1
            })

            # after:
            self.assertEqual(booking.get_price(), 340.0)
            self.assertEqual(booking.booker_fee, 17.0)
            self.assertEqual(booking.dj_fee, 34.0)
            self.assertEqual(booking.get_discounts_sum(), 10.0)

            # 2nd promocode

            pr2 = self.test_data_service.create_random_promocode(
                promocode_type=PromocodeType.PERCENT.value,
                amount=20
            )

            response = self.booker_client.post(
                reverse('booking:booking_promocode_application', args=(booking.pk,)),
                data={'promocode': pr2.code}
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

            booking = Booking.objects.get(pk=booking.pk)

            self.assertEqual(len(booking.applied_discounts.keys()), 2)

            self.assertEqual(booking.applied_discounts[str(pr2.pk)], {
                'amount': 20.0,
                'calculated_amount': 68.0,
                'date': '13-Mar-2021 (00:00:00.000000)',
                'price': 340.0,
                'promocode_type': 2
            })

            # after:
            self.assertEqual(booking.get_price(), 272.0)
            self.assertEqual(booking.booker_fee, 13.6)
            self.assertEqual(booking.dj_fee, 27.2)
            self.assertEqual(booking.get_discounts_sum(), 78.0)

            # 3rd promocode

            pr3 = self.test_data_service.create_random_promocode(
                promocode_type=PromocodeType.FIXED.value,
                amount=272
            )

            response = self.booker_client.post(
                reverse('booking:booking_promocode_application', args=(booking.pk,)),
                data={'promocode': pr3.code}
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

            booking = Booking.objects.get(pk=booking.pk)

            self.assertEqual(len(booking.applied_discounts.keys()), 3)

            self.assertEqual(booking.applied_discounts[str(pr3.pk)], {
                'amount': 272.0,
                'calculated_amount': 272.0,
                'date': '13-Mar-2021 (00:00:00.000000)',
                'price': 272.0,
                'promocode_type': 1
            })

            # after:
            self.assertEqual(booking.get_price(), 0)
            self.assertEqual(booking.booker_fee, 0)
            self.assertEqual(booking.dj_fee, 0)
            self.assertEqual(booking.get_discounts_sum(), 350.0)

    def test_booking_promocode_application_price_calculation_full_discount(self):

        # fixed promocode
        with patch.object(timezone, 'now', return_value=make_aware(datetime(2021, 3, 13, 0, 0))):

            booking = self.test_data_service.create_custom_booking(
                account_booker=self.booker_user.get_account(),
                account_dj=self.dj_user.get_account()
            )

            pr1 = self.test_data_service.create_random_promocode(
                promocode_type=PromocodeType.FIXED.value,
                amount=350
            )

            response = self.booker_client.post(
                reverse('booking:booking_promocode_application', args=(booking.pk,)),
                data={'promocode': pr1.code}
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

            booking = Booking.objects.get(pk=booking.pk)

            self.assertEqual(len(booking.applied_discounts.keys()), 1)

            (k, v), = booking.applied_discounts.items()

            self.assertEqual(v, {
                'amount': 350.0,
                'calculated_amount': 350.0,
                'date': '13-Mar-2021 (00:00:00.000000)',
                'price': 350.0,
                'promocode_type': 1
            })

            # after:
            self.assertEqual(booking.get_price(), 0)
            self.assertEqual(booking.booker_fee, 0)
            self.assertEqual(booking.dj_fee, 0)
            self.assertEqual(booking.get_discounts_sum(), 350.0)

        # percent promocode
        with patch.object(timezone, 'now', return_value=make_aware(datetime(2021, 3, 13, 0, 0))):

            booking = self.test_data_service.create_custom_booking(
                account_booker=self.booker_user.get_account(),
                account_dj=self.dj_user.get_account()
            )

            pr1 = self.test_data_service.create_random_promocode(
                promocode_type=PromocodeType.PERCENT.value,
                amount=100
            )

            response = self.booker_client.post(
                reverse('booking:booking_promocode_application', args=(booking.pk,)),
                data={'promocode': pr1.code}
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

            booking = Booking.objects.get(pk=booking.pk)

            self.assertEqual(len(booking.applied_discounts.keys()), 1)

            (k, v), = booking.applied_discounts.items()

            self.assertEqual(v, {
                'amount': 100.0,
                'calculated_amount': 350.0,
                'date': '13-Mar-2021 (00:00:00.000000)',
                'price': 350.0,
                'promocode_type': 2
            })

            # after:
            self.assertEqual(booking.get_price(), 0)
            self.assertEqual(booking.booker_fee, 0)
            self.assertEqual(booking.dj_fee, 0)
            self.assertEqual(booking.get_discounts_sum(), 350.0)

    # PROMOCODE IN CHECKOUT (TRANSACTIONS TESTS)

    def test_create_booking_paid_2step_checkout_with_promocode(self):

        with patch.object(timezone, 'now', return_value=make_aware(datetime(2021, 3, 13, 0, 0))):

            # booking
            data = {
                'account_dj': self.dj_user.get_account().uuid,
                'comment': 'Comment',
                'date': '2021-03-13',
                'time': '18:00',
                'duration': 180,
                'gig_type': 1,
                'listeners': 2,
                'out_door_play': 1,
                'in_door_play': 1,
                'music_list': [1]
            }
            response = self.__create_booking_response(data=data)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertEqual(response.data['payment_intent_id'], '')
            self.assertEqual(response.data['total_cost'], 350)
            self.assertEqual(response.data['discounts'], 0)
            self.assertEqual(response.data['performance_cost'], 332.5)  # total_cost - booker_fee
            self.assertEqual(response.data['performer_cost'], 30.0)  # 10 * 3 hours
            self.assertEqual(response.data['price_per_hour'], 10.0)
            self.assertEqual(response.data['dj_fee'], 35.0)
            self.assertEqual(response.data['booker_fee'], 17.5)
            self.assertEqual(response.data['sum_to_pay_from_balance'], 0)
            self.assertEqual(response.data['sum_to_pay_from_card'], 350.0)

            # checkout page

            booking_id = response.data['id']

            # 1. promocode application

            pr1 = self.test_data_service.create_random_promocode(
                promocode_type=PromocodeType.FIXED.value,
                amount=10  # $,
            )

            response = self.booker_client.post(
                reverse('booking:booking_promocode_application', args=(booking_id,)),
                data={'promocode': pr1.code}
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

            # 2. payment

            response = self.booker_client.post(
                reverse('booking:booking_pay', args=(booking_id,)),
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertIn('pi_', response.data['payment_intent_id'])
            self.assertIn('secret', response.data['payment_intent_client_secret'])
            self.assertEqual(response.data['status'], Booking.Status.NOT_PAID)

            # step similar to action on the frontend
            self.__confirm_payment_intent(
                payment_intent=response.data['payment_intent_id']
            )

            # after stripe webhook is called
            booking = Booking.objects.get(pk=response.data['id'])
            PaymentService().pay_booking(booking.payment_intent_id, amount=int(booking.get_price() * 100))

            booking = Booking.objects.get(pk=response.data['id'])
            self.assertEqual(booking.status, Booking.Status.PAID)

            self.assertEqual(Transaction.objects.count(), 3)

            transactions = Transaction.objects.filter(entity_pk=booking.pk).order_by('id')

            self.assertEqual(transactions[0].purpose, TransactionPurposes.PAYMENT)
            self.assertEqual(transactions[0].amount, 34000)

            self.assertEqual(transactions[1].purpose, TransactionPurposes.BOOKING_ESCROW)
            self.assertEqual(transactions[1].amount, -32300)

            self.assertEqual(transactions[2].purpose, TransactionPurposes.BOOKING_FEE_FOR_BOOKER)
            self.assertEqual(transactions[2].amount, -1700)

    def test_create_booking_paid_2step_checkout_stripe_and_user_balance_with_promocode(self):

        with patch.object(timezone, 'now', return_value=make_aware(datetime(2021, 3, 13, 0, 0))):

            # user balance
            self.test_data_service.create_random_transaction(
                purpose=TransactionPurposes.PAYMENT,
                amount=10000,
                user=self.booker_user
            )

            # booking
            data = {
                'account_dj': self.dj_user.get_account().uuid,
                'comment': 'Comment',
                'date': '2021-03-13',
                'time': '18:00',
                'duration': 180,
                'gig_type': 1,
                'listeners': 2,
                'out_door_play': 1,
                'in_door_play': 1,
                'music_list': [1]
            }
            response = self.__create_booking_response(data=data)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertEqual(response.data['payment_intent_id'], '')
            self.assertEqual(response.data['total_cost'], 350)
            self.assertEqual(response.data['discounts'], 0)
            self.assertEqual(response.data['performance_cost'], 332.5)  # total_cost - booker_fee
            self.assertEqual(response.data['performer_cost'], 30.0)  # 10 * 3 hours
            self.assertEqual(response.data['price_per_hour'], 10.0)
            self.assertEqual(response.data['dj_fee'], 35.0)
            self.assertEqual(response.data['booker_fee'], 17.5)
            self.assertEqual(response.data['sum_to_pay_from_balance'], 100.0)
            self.assertEqual(response.data['sum_to_pay_from_card'], 250.0)

            # checkout page

            booking_id = response.data['id']

            # 1. promocode application

            pr1 = self.test_data_service.create_random_promocode(
                promocode_type=PromocodeType.FIXED.value,
                amount=90  # $
            )

            response = self.booker_client.post(
                reverse('booking:booking_promocode_application', args=(booking_id,)),
                data={'promocode': pr1.code}
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

            # get updated data for checkout page after promocode is applied:

            response = self.booker_client.get(
                reverse('booking:booking_retrieve_update', args=(booking_id,))
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data['total_cost'], 260)
            self.assertEqual(response.data['discounts'], 90)
            self.assertEqual(response.data['performance_cost'], 247)  # total_cost - booker_fee
            self.assertEqual(response.data['performer_cost'], 30.0)  # 10 * 3 hours
            self.assertEqual(response.data['price_per_hour'], 10.0)
            self.assertEqual(response.data['dj_fee'], 26.0)
            self.assertEqual(response.data['booker_fee'], 13.0)
            self.assertEqual(response.data['sum_to_pay_from_balance'], 100.0)
            self.assertEqual(response.data['sum_to_pay_from_card'], 160.0)

            # 2. payment

            response = self.booker_client.post(
                reverse('booking:booking_pay', args=(booking_id,)),
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertIn('pi_', response.data['payment_intent_id'])
            self.assertIn('secret', response.data['payment_intent_client_secret'])
            self.assertEqual(response.data['status'], Booking.Status.NOT_PAID)

            # step similar to action on the frontend
            self.__confirm_payment_intent(
                payment_intent=response.data['payment_intent_id']
            )

            # after stripe webhook is called
            booking = Booking.objects.get(pk=response.data['id'])

            sum_to_pay_from_card = PaymentService()._convert_amount_to_cents(
                booking.get_price() - booking.sum_to_pay_from_balance
            )
            PaymentService().pay_booking(
                booking.payment_intent_id,
                amount=sum_to_pay_from_card
            )

            booking = Booking.objects.get(pk=response.data['id'])
            self.assertEqual(booking.status, Booking.Status.PAID)

            self.assertEqual(Transaction.objects.count(), 4)

            transactions = Transaction.objects.filter(user=self.booker_user).order_by('id')

            self.assertEqual(transactions[0].purpose, TransactionPurposes.PAYMENT)
            self.assertEqual(transactions[0].amount, 10_000)

            self.assertEqual(transactions[1].purpose, TransactionPurposes.PAYMENT)
            self.assertEqual(transactions[1].amount, 16_000)

            self.assertEqual(transactions[2].purpose, TransactionPurposes.BOOKING_ESCROW)
            self.assertEqual(transactions[2].amount, -24_700)

            self.assertEqual(transactions[3].purpose, TransactionPurposes.BOOKING_FEE_FOR_BOOKER)
            self.assertEqual(transactions[3].amount, -1300)

    def test_create_booking_paid_2step_checkout_only_user_balance_with_promocode(self):

        with patch.object(timezone, 'now', return_value=make_aware(datetime(2021, 3, 13, 0, 0))):

            # user balance
            self.test_data_service.create_random_transaction(
                purpose=TransactionPurposes.PAYMENT,
                amount=35_000,
                user=self.booker_user
            )

            # booking
            data = {
                'account_dj': self.dj_user.get_account().uuid,
                'comment': 'Comment',
                'date': '2021-03-13',
                'time': '18:00',
                'duration': 180,
                'gig_type': 1,
                'listeners': 2,
                'out_door_play': 1,
                'in_door_play': 1,
                'music_list': [1]
            }
            response = self.__create_booking_response(data=data)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertEqual(response.data['payment_intent_id'], '')
            self.assertEqual(response.data['total_cost'], 350)
            self.assertEqual(response.data['discounts'], 0)
            self.assertEqual(response.data['performance_cost'], 332.5)  # total_cost - booker_fee
            self.assertEqual(response.data['performer_cost'], 30.0)  # 10 * 3 hours
            self.assertEqual(response.data['price_per_hour'], 10.0)
            self.assertEqual(response.data['dj_fee'], 35.0)
            self.assertEqual(response.data['booker_fee'], 17.5)
            self.assertEqual(response.data['sum_to_pay_from_balance'], 350.0)
            self.assertEqual(response.data['sum_to_pay_from_card'], 0)

            # checkout page

            booking_id = response.data['id']

            # 1. promocode application

            pr1 = self.test_data_service.create_random_promocode(
                promocode_type=PromocodeType.FIXED.value,
                amount=10  # $,
            )

            response = self.booker_client.post(
                reverse('booking:booking_promocode_application', args=(booking_id,)),
                data={'promocode': pr1.code}
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

            # 2. payment

            response = self.booker_client.post(
                reverse('booking:booking_pay', args=(booking_id,)),
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data['payment_intent_id'], '')
            self.assertEqual(response.data['payment_intent_client_secret'], '')
            self.assertEqual(response.data['status'], Booking.Status.PAID)

            # step similar to action on the frontend
            # (nothing is done on the frontend)

            booking = Booking.objects.get(pk=response.data['id'])
            self.assertEqual(booking.status, Booking.Status.PAID)

            self.assertEqual(Transaction.objects.count(), 3)

            transactions = Transaction.objects.filter(user=self.booker_user).order_by('id')

            self.assertEqual(transactions[0].purpose, TransactionPurposes.PAYMENT)
            self.assertEqual(transactions[0].amount, 35000)

            self.assertEqual(transactions[1].purpose, TransactionPurposes.BOOKING_ESCROW)
            self.assertEqual(transactions[1].amount, -32300)

            self.assertEqual(transactions[2].purpose, TransactionPurposes.BOOKING_FEE_FOR_BOOKER)
            self.assertEqual(transactions[2].amount, -1700)

    # FULL DISCOUNT - TRANSACTIONS TEST

    def test_create_booking_paid_2step_checkout_with_promocode_full_discount(self):

        with patch.object(timezone, 'now', return_value=make_aware(datetime(2021, 3, 13, 0, 0))):

            # booking
            data = {
                'account_dj': self.dj_user.get_account().uuid,
                'comment': 'Comment',
                'date': '2021-03-13',
                'time': '18:00',
                'duration': 180,
                'gig_type': 1,
                'listeners': 2,
                'out_door_play': 1,
                'in_door_play': 1,
                'music_list': [1]
            }
            response = self.__create_booking_response(data=data)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertEqual(response.data['payment_intent_id'], '')
            self.assertEqual(response.data['total_cost'], 350)
            self.assertEqual(response.data['discounts'], 0)
            self.assertEqual(response.data['performance_cost'], 332.5)  # total_cost - booker_fee
            self.assertEqual(response.data['performer_cost'], 30.0)  # 10 * 3 hours
            self.assertEqual(response.data['price_per_hour'], 10.0)
            self.assertEqual(response.data['dj_fee'], 35.0)
            self.assertEqual(response.data['booker_fee'], 17.5)
            self.assertEqual(response.data['sum_to_pay_from_balance'], 0)
            self.assertEqual(response.data['sum_to_pay_from_card'], 350.0)

            # checkout page

            booking_id = response.data['id']

            # 1. promocode application

            pr1 = self.test_data_service.create_random_promocode(
                promocode_type=PromocodeType.FIXED.value,
                amount=350  # $,
            )

            response = self.booker_client.post(
                reverse('booking:booking_promocode_application', args=(booking_id,)),
                data={'promocode': pr1.code}
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

            # 2. payment (it will be as FROM USER BALANCE - no stripe involved if price is 0)

            response = self.booker_client.post(
                reverse('booking:booking_pay', args=(booking_id,)),
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data['payment_intent_id'], '')
            self.assertEqual(response.data['payment_intent_client_secret'], '')
            self.assertEqual(response.data['status'], Booking.Status.PAID)

            booking = Booking.objects.get(pk=response.data['id'])
            PaymentService().transfer_money_for_dj(booking=booking)

            self.assertEqual(Transaction.objects.count(), 4)

            transactions = Transaction.objects.filter(entity_pk=booking.pk).order_by('id')

            self.assertEqual(transactions[0].purpose, TransactionPurposes.BOOKING_ESCROW)
            self.assertEqual(transactions[0].amount, 0)

            self.assertEqual(transactions[1].purpose, TransactionPurposes.BOOKING_FEE_FOR_BOOKER)
            self.assertEqual(transactions[1].amount, 0)

            self.assertEqual(transactions[2].purpose, TransactionPurposes.PAYMENT_TO_USER)
            self.assertEqual(transactions[2].amount, 0)

            self.assertEqual(transactions[3].purpose, TransactionPurposes.BOOKING_FEE_FOR_DJ)
            self.assertEqual(transactions[3].amount, 0)


class BookingListTestCase(AuthClientTestCase):

    def __list_booking_response(self, filters=None):
        return self.booker_client.get(reverse('booking:booking_list_create'), filters)

    def __test_list_booking_response_with_status(self, booking_status: int):
        response = self.__list_booking_response(filters={'status': booking_status})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['count'],
            self.booker_user.get_account().bookings_booker.filter(status=booking_status).count()
        )

    def test_empty_list(self):
        response = self.__list_booking_response()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(dict(response.data), {'count': 0, 'next': None, 'previous': None, 'results': []})

    def test_list(self):
        self.test_data_service.create_random_bookings(count=30)
        response = self.__list_booking_response()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['count'], self.booker_user.get_account().bookings_booker.count())

        self.__test_list_booking_response_with_status(booking_status=1)
        self.__test_list_booking_response_with_status(booking_status=2)

    def test_booking_retrieve_ordering(self):
        self.test_data_service.create_random_bookings(count=30)
        response = self.booker_client.get(reverse("booking:booking_list_create"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        last_date = None
        for result in response.data["results"]:
            curr_date = result["date"]
            if last_date is not None:
                self.assertLess(curr_date, last_date)
            last_date = curr_date


class BookingUpdateTestCase(AuthClientTestCase):

    @staticmethod
    def __confirm_payment_intent(payment_intent: str):
        PaymentService().provider.PaymentIntent.confirm(payment_intent, payment_method='pm_card_visa')

    def test_booking_decline_by_dj(self):
        data = {'decline_comment': DECLINE_COMMENT}
        booking = self.test_data_service.get_random_booking_by_account(
            account=self.dj_user.get_account(),
            count=5
        )
        self.__confirm_payment_intent(payment_intent=booking.payment_intent_id)
        response = self.dj_client.post(reverse('booking:booking_decline', args=[booking.pk]), data=data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        declined_booking = Booking.objects.get(pk=booking.pk)
        self.assertEqual(response.data['status'], 4)
        self.assertEqual(response.data['status'], declined_booking.status)
        self.assertEqual(response.data['decline_comment'], data['decline_comment'])
        self.assertEqual(response.data['decline_comment'], declined_booking.decline_comment)

    def test_booking_decline_by_booker(self):
        data = {'decline_comment': DECLINE_COMMENT}
        booking = self.test_data_service.get_random_booking_by_account(
            account=self.booker_user.get_account(),
            count=5
        )
        self.__confirm_payment_intent(payment_intent=booking.payment_intent_id)
        response = self.booker_client.post(reverse('booking:booking_decline', args=[booking.pk]), data=data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        declined_booking = Booking.objects.get(pk=booking.pk)
        self.assertEqual(response.data['status'], Booking.Status.DECLINED_BY_BOOKER)
        self.assertEqual(response.data['status'], declined_booking.status)
        self.assertEqual(response.data['decline_comment'], data['decline_comment'])
        self.assertEqual(response.data['decline_comment'], declined_booking.decline_comment)

    def test_booking_accept_by_dj(self):
        booking = self.test_data_service.get_random_booking_by_account(
            account=self.dj_user.get_account(),
            count=5
        )
        self.__confirm_payment_intent(payment_intent=booking.payment_intent_id)
        response = self.dj_client.post(
            reverse('booking:booking_accept', args=[booking.pk]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        accepted_booking = Booking.objects.get(pk=booking.pk)
        self.assertEqual(response.data['status'], Booking.Status.ACCEPTED_BY_DJ)
        self.assertEqual(response.data['status'], accepted_booking.status)

    def test_booking_accept_by_another_user(self):
        booking = self.test_data_service.create_random_bookings(count=1, paid=True)[0]
        self.__confirm_payment_intent(payment_intent=booking.payment_intent_id)
        response = self.booker_client.post(
            reverse('booking:booking_accept', args=[booking.pk]))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_booking_accept_by_dj_with_booker_profile(self):
        self.test_data_service.create_random_bookings(count=20)
        queryset = Booking.objects.filter_by_dj(
            account=self.dj_user_with_booker_profile.get_account()
        )
        booking = random.choice(queryset)
        payment_intent = PaymentService().create_payment_intent(
            booking=booking,
            amount=booking.get_price()
        )
        booking.payment_intent_id = payment_intent.stripe_id
        booking.payment_intent_client_secret = payment_intent['client_secret']
        booking.save()
        self.__confirm_payment_intent(payment_intent=booking.payment_intent_id)
        response = self.dj_and_booker_client.post(
            reverse('booking:booking_accept', args=[booking.pk]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_booking_review_by_booker_and_dj(self):
        data = {
            'rating': random.randint(1, 5),
            'comment': 'Something comment'
        }
        booking = self.test_data_service.get_random_booking_by_booker_and_dj_accounts(
            booker_account=self.booker_user.get_account(),
            dj_account=self.dj_user.get_account().uuid,
            count=30
        )
        booking.status = Booking.Status.SUCCESS
        booking.save()
        response = self.booker_client.post(reverse('booking:booking_review_create', args=[booking.pk]), data=data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['rating'], data['rating'])
        self.assertEqual(response.data['comment'], data['comment'])
        self.assertEqual(response.data['booking'], booking.pk)

        response = self.dj_client.post(reverse('booking:booking_review_create', args=[booking.pk]), data=data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['rating'], data['rating'])
        self.assertEqual(response.data['comment'], data['comment'])
        self.assertEqual(response.data['booking'], booking.pk)

    def test_create_booking_review_with_invalid_status(self):
        data = {
            'rating': random.randint(1, 5),
            'comment': 'Something comment'
        }
        booking = self.test_data_service.get_random_booking_by_booker_and_dj_accounts(
            booker_account=self.booker_user.get_account(),
            dj_account=self.dj_user.get_account().uuid,
            count=30
        )
        response = self.booker_client.post(reverse('booking:booking_review_create', args=[booking.pk]), data=data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['rating'], data['rating'])
        self.assertEqual(response.data['comment'], data['comment'])
        self.assertEqual(response.data['booking'], booking.pk)

    def test_booking_delete_by_timeout_payment(self):
        booking = self.test_data_service.create_random_bookings(count=1, paid=True)[0]
        booking.created_at = timezone.now() - timedelta(minutes=10)
        booking.save()

        self.assertNotEqual(booking.payment_intent_id, '')
        self.assertEqual(booking.status, Booking.Status.NOT_PAID)

        booking_delete_by_timeout_payment()

        self.assertEqual(Booking.objects.filter(pk=booking.pk).count(), 0)
        self.assertEqual(
            PaymentService().provider.PaymentIntent.retrieve(booking.payment_intent_id)['status'],
            'canceled'
        )

    def test_booking_paid_from_balance_delete_by_timeout_payment(self):
        booking = self.test_data_service.create_custom_booking(
            account_booker=self.booker_user.get_accounts().first(),
            account_dj=self.dj_user.get_accounts().first(),
        )
        booking.created_at = timezone.now() - timedelta(minutes=10)
        booking.save()

        self.assertEqual(booking.payment_intent_id, '')
        self.assertEqual(booking.status, Booking.Status.NOT_PAID)

        booking_delete_by_timeout_payment()

        self.assertEqual(Booking.objects.filter(pk=booking.pk).count(), 0)

    def test_booking_success_for_accepted_by_dj_and_performed(self):
        booking = self.test_data_service.create_random_bookings(count=1, paid=True)[0]
        performance_date = (timezone.now() - timedelta(minutes=10)).replace(microsecond=0)
        booking.date, booking.time = performance_date.date(), performance_date.time()
        booking.status = Booking.Status.ACCEPTED_BY_DJ
        booking.save()
        task_for_accepted_by_dj_bookings()
        performed_booking = Booking.objects.get(pk=booking.pk)
        self.assertEqual(performed_booking.status, Booking.Status.SUCCESS)

    def test_booking_success_for_accepted_by_dj_and_not_performed(self):
        booking = self.test_data_service.create_random_bookings(count=1, paid=True)[0]
        performance_date = (timezone.now() + timedelta(minutes=10)).replace(microsecond=0)
        booking.date, booking.time = performance_date.date(), performance_date.time()
        booking.status = Booking.Status.ACCEPTED_BY_DJ
        booking.save()
        task_for_accepted_by_dj_bookings()
        performed_booking = Booking.objects.get(pk=booking.pk)
        self.assertNotEqual(performed_booking.status, Booking.Status.SUCCESS)

    def test_notify_on_hours_before_start_for_accepted_by_dj(self):

        mail.outbox = []
        self.test_data_service.create_random_bookings(count=1)
        booking = Booking.objects.first()
        booking.status = Booking.Status.ACCEPTED_BY_DJ
        performance_date = (timezone.now() + timedelta(hours=48)).replace(microsecond=0)
        booking.date, booking.time = performance_date.date(), performance_date.time()
        booking.save()

        # 12, 24, 36 are used for sending
        for hours in [11, 12, 13, 23, 24, 25, 35, 36, 37]:
            cur_time = (timezone.now() + timedelta(hours=hours)).replace(microsecond=0)
            with patch.object(timezone, 'now', return_value=cur_time) as mock_now:
                task_for_accepted_by_dj_bookings()

        outbox = mail.outbox
        self.assertEqual(len(outbox), 6)  # 2 emails (dj and booker) for 3 times

        self.assertEqual(outbox[0].subject, SEND_BEFORE_START_EVENT_TO_BOOKER.format(
            date=booking.date,
            stage_name=booking.account_dj.dj_profile.stage_name
        ))
        self.assertEqual(outbox[0].to, [booking.account_booker.user.email])

    def test_task_for_paid_bookings_before_48_hours(self):
        booking = self.test_data_service.create_random_bookings(count=1, paid=True)[0]
        self.__confirm_payment_intent(payment_intent=booking.payment_intent_id)
        booking.created_at = timezone.now() - timedelta(hours=47, minutes=59)
        performance_date = (timezone.now() + timedelta(minutes=10)).replace(microsecond=0)
        booking.date, booking.time = performance_date.date(), performance_date.time()
        booking.status = Booking.Status.PAID
        booking.save()
        expiring_booking_id = booking.pk
        task_for_paid_bookings()
        self.assertTrue(Booking.objects.filter(pk=expiring_booking_id).exists())

    def test_task_for_paid_bookings_expired_after_48_hours(self):
        booking = self.test_data_service.create_random_bookings(count=1, paid=True)[0]
        self.__confirm_payment_intent(payment_intent=booking.payment_intent_id)
        booking.created_at = timezone.now() - timedelta(hours=48, minutes=1)
        booking.status = Booking.Status.PAID
        booking.save()
        task_for_paid_bookings()
        not_accepted_booking = Booking.objects.get(pk=booking.pk)
        self.assertEqual(not_accepted_booking.status, Booking.Status.REJECTED)

    def test_task_for_paid_bookings_not_performed_in_time_before_48_hours(self):
        booking = self.test_data_service.create_random_bookings(count=1, paid=True)[0]
        self.__confirm_payment_intent(payment_intent=booking.payment_intent_id)
        booking.created_at = timezone.now() - timedelta(hours=10)
        performance_date = (
            timezone.now() - timedelta(minutes=10)).replace(microsecond=0)
        booking.date, booking.time = performance_date.date(), performance_date.time()
        booking.status = Booking.Status.PAID
        booking.save()
        task_for_paid_bookings()
        not_accepted_booking = Booking.objects.get(pk=booking.pk)
        self.assertEqual(not_accepted_booking.status, Booking.Status.REJECTED)

    def test_task_for_paid_bookings_not_yet_performed_before_48_hours(self):
        booking = self.test_data_service.create_random_bookings(count=1, paid=True)[0]
        self.__confirm_payment_intent(payment_intent=booking.payment_intent_id)
        booking.created_at = timezone.now() - timedelta(hours=10)
        performance_date = (
            timezone.now() + timedelta(minutes=10)).replace(microsecond=0)
        booking.date, booking.time = performance_date.date(), performance_date.time()
        booking.status = Booking.Status.PAID
        booking.save()
        expiring_booking_id = booking.pk
        task_for_paid_bookings()
        self.assertTrue(Booking.objects.filter(pk=expiring_booking_id).exists())

    def test_dj_decline_booking_create_by_this_dj(self):
        decline_data = {'decline_comment': DECLINE_COMMENT}
        self.test_data_service.create_dj_profiles(count=10)
        dj_profile = random.choice(DJProfile.objects.all())
        with patch.object(timezone, 'now', return_value=make_aware(datetime(2021, 3, 1, 0, 0))):
            data = {
                'account_dj': dj_profile.account.uuid,
                'comment': 'Comment',
                'date': '2021-03-13',
                'time': '18:00',
                'duration': 1200,
                'gig_type': 1,
                'listeners': 2,
                'out_door_play': 1,
                'in_door_play': 1,
                'music_list': [1]
            }
            response = self.dj_client.post(reverse('booking:booking_list_create'), data=data)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertEqual(response.data['comment'], data['comment'])

            response = self.dj_client.post(
                reverse('booking:booking_pay', args=(response.data['id'],)),
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

            booking = Booking.objects.get(pk=response.data['id'])
            self.__confirm_payment_intent(payment_intent=booking.payment_intent_id)

            response = self.dj_client.post(reverse('booking:booking_decline', args=[booking.pk]), data=decline_data)
            self.assertEqual(response.status_code, status.HTTP_200_OK)

            declined_booking = Booking.objects.get(pk=booking.pk)
            self.assertEqual(declined_booking.status, Booking.Status.DECLINED_BY_BOOKER)

    def test_unpaid_booking_decline_by_booker(self):
        data = {'decline_comment': DECLINE_COMMENT}
        unpaid_booking = self.test_data_service.get_random_booking_by_account(
            account=self.booker_user.get_account(),
            count=5
        )
        unpaid_booking.status = Booking.Status.NOT_PAID
        unpaid_booking.save()
        response = self.booker_client.post(
            reverse('booking:booking_decline', args=[unpaid_booking.pk]), data=data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        declined_booking = Booking.objects.get(pk=unpaid_booking.pk)
        self.assertEqual(response.data['status'], Booking.Status.DECLINED_BY_BOOKER)
        self.assertEqual(response.data['status'], declined_booking.status)
        self.assertEqual(
            response.data['decline_comment'], data['decline_comment'])
        self.assertEqual(
            response.data['decline_comment'], declined_booking.decline_comment)

    def test_booking_cancel_by_dj(self):
        booking = self.test_data_service.get_random_booking_by_account(
            account=self.dj_user.get_account(),
            count=5
        )
        self.__confirm_payment_intent(payment_intent=booking.payment_intent_id)
        booking.accept()
        booking.save()
        response = self.dj_client.post(
            reverse('booking:booking_cancel', args=[booking.pk]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        canceled_booking = Booking.objects.get(pk=booking.pk)
        self.assertEqual(response.data['status'],
                         Booking.Status.CANCELED_BY_DJ)
        self.assertEqual(response.data['status'], canceled_booking.status)

    def test_booking_cancel_by_booker(self):
        booking = self.test_data_service.get_random_booking_by_account(
            account=self.booker_user.get_account(),
            count=10
        )
        self.__confirm_payment_intent(payment_intent=booking.payment_intent_id)
        booking.accept()
        booking.save()
        response = self.booker_client.post(
            reverse('booking:booking_cancel', args=[booking.pk]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        canceled_booking = Booking.objects.get(pk=booking.pk)
        self.assertEqual(response.data['status'],
                         Booking.Status.CANCELED_BY_BOOKER)
        self.assertEqual(response.data['status'], canceled_booking.status)

    def test_booking_cancel_for_selected_statuses_only(self):
        booking = self.test_data_service.get_random_booking_by_account(
            account=self.booker_user.get_account(),
            count=5
        )
        self.__confirm_payment_intent(payment_intent=booking.payment_intent_id)

        valid_statuses = [Booking.Status.ACCEPTED_BY_DJ]
        for booking_status in valid_statuses:
            booking.status = booking_status
            booking.save()
            response = self.booker_client.post(
                reverse('booking:booking_cancel', args=[booking.pk]))
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            changed_booking = Booking.objects.get(pk=booking.pk)
            self.assertEqual(response.data['status'], Booking.Status.CANCELED_BY_BOOKER)
            self.assertEqual(changed_booking.status, Booking.Status.CANCELED_BY_BOOKER)

        valid_statuses += [Booking.Status.CANCELED_BY_BOOKER]
        not_valid_statuses = [s for s in list(Booking.Status) if s not in valid_statuses]
        for booking_status in not_valid_statuses:
            booking.status = booking_status
            booking.save()
            response = self.booker_client.post(
                reverse('booking:booking_cancel', args=[booking.pk]))
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            changed_booking = Booking.objects.get(pk=booking.pk)
            self.assertNotEqual(response.data['status'], Booking.Status.CANCELED_BY_BOOKER)
            self.assertNotEqual(changed_booking.status, Booking.Status.CANCELED_BY_BOOKER)

    def test_booking_decline_for_selected_statuses_only(self):
        data = {'decline_comment': DECLINE_COMMENT}
        booking = self.test_data_service.get_random_booking_by_account(
            account=self.booker_user.get_account(),
            count=5
        )
        self.__confirm_payment_intent(payment_intent=booking.payment_intent_id)

        valid_statuses = [Booking.Status.NOT_PAID, Booking.Status.PAID]
        for booking_status in valid_statuses:
            booking.status = booking_status
            booking.save()
            response = self.booker_client.post(
                reverse('booking:booking_decline', args=[booking.pk]), data=data)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            changed_booking = Booking.objects.get(pk=booking.pk)
            self.assertEqual(
                response.data['status'], Booking.Status.DECLINED_BY_BOOKER)
            self.assertEqual(changed_booking.status,
                             Booking.Status.DECLINED_BY_BOOKER)

        valid_statuses += [Booking.Status.DECLINED_BY_BOOKER]
        not_valid_statuses = [s for s in list(Booking.Status) if s not in valid_statuses]
        for booking_status in not_valid_statuses:
            booking.status = booking_status
            booking.save()
            response = self.booker_client.post(
                reverse('booking:booking_decline', args=[booking.pk]), data=data)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            changed_booking = Booking.objects.get(pk=booking.pk)
            self.assertNotEqual(
                response.data['status'], Booking.Status.DECLINED_BY_BOOKER)
            self.assertNotEqual(changed_booking.status,
                                Booking.Status.DECLINED_BY_BOOKER)

    def test_remind_about_booking_rating_reminders_for_hours_work(self):
        mail.outbox = []
        self.test_data_service.create_random_bookings(count=2)
        bookings = Booking.objects.all().order_by('pk')

        for i, hours in enumerate([12, 96]):
            performance_date = (timezone.now() - timedelta(hours=hours)).replace(microsecond=0)
            booking = bookings[i]
            booking.date, booking.time = performance_date.date(), performance_date.time()
            booking.status = random.choice([
                Booking.Status.SUCCESS,
                Booking.Status.COMPLETED,
                Booking.Status.IN_DISPUTE,
                Booking.Status.DISPUTED
            ])
            booking.save()

        remind_about_booking_rating()

        outbox = mail.outbox
        self.assertEqual(len(outbox), 4)
        self.assertEqual(outbox[0].subject, SEND_REMINDER_TO_BOOKER_ABOUT_RATING.format(
            stage_name=booking.account_dj.dj_profile.stage_name
        ))
        self.assertEqual(outbox[0].to, [bookings[0].account_booker.user.email])

    def test_remind_about_booking_rating_no_reminders(self):
        mail.outbox = []
        self.test_data_service.create_random_bookings(count=2)
        bookings = Booking.objects.all().order_by('pk')

        for i, hours in enumerate([12, 96]):
            performance_date = (
                timezone.now() - timedelta(hours=hours)).replace(microsecond=0)
            booking = bookings[i]
            booking.date, booking.time = performance_date.date(), performance_date.time()
            statuses = set(Booking.Status) - set([
                Booking.Status.SUCCESS,
                Booking.Status.COMPLETED,
                Booking.Status.IN_DISPUTE,
                Booking.Status.DISPUTED
            ])
            booking.status = random.choice(list(statuses))
            booking.save()

        remind_about_booking_rating()
        self.assertEqual(len(mail.outbox), 0)

        # various hours
        HOURS = list(range(0, 120))
        HOURS.remove(12)
        HOURS.remove(96)
        for hours in HOURS:
            performance_date = (
                timezone.now() - timedelta(hours=hours)).replace(microsecond=0)
            booking = bookings[i]
            booking.date, booking.time = performance_date.date(), performance_date.time()
            booking.status = random.choice([
                Booking.Status.SUCCESS,
                Booking.Status.COMPLETED,
                Booking.Status.IN_DISPUTE,
                Booking.Status.DISPUTED
            ])
            booking.save()

            remind_about_booking_rating()

        self.assertEqual(len(mail.outbox), 0)

        # reviews exist
        br = BookingReview(booking=bookings[0], rating=5, is_by_booker=True)
        br.save()
        br = BookingReview(booking=bookings[0], rating=5, is_by_booker=False)
        br.save()
        br = BookingReview(booking=bookings[1], rating=5, is_by_booker=True)
        br.save()
        br = BookingReview(booking=bookings[1], rating=5, is_by_booker=False)
        br.save()

        for i, hours in enumerate([12, 96]):
            performance_date = (
                timezone.now() - timedelta(hours=hours)).replace(microsecond=0)
            booking = bookings[i]
            booking.date, booking.time = performance_date.date(), performance_date.time()
            booking.status = random.choice([
                Booking.Status.SUCCESS,
                Booking.Status.COMPLETED,
                Booking.Status.IN_DISPUTE,
                Booking.Status.DISPUTED
            ])
            booking.save()

        remind_about_booking_rating()
        self.assertEqual(len(mail.outbox), 0)

    def test_disable_ratings_for_old_bookings(self):

        self.test_data_service.create_random_bookings(count=2)
        bookings = Booking.objects.all().order_by('pk')

        self.assertTrue(bookings[0].can_be_rated)
        self.assertTrue(bookings[1].can_be_rated)

        performance_date = (
            timezone.now() - timedelta(hours=119, minutes=59, seconds=59)
        ).replace(microsecond=0)
        booking = bookings[0]
        booking.date, booking.time = performance_date.date(), performance_date.time()
        booking.save()

        performance_date = (
            timezone.now() - timedelta(hours=120, minutes=0, seconds=0)
        ).replace(microsecond=0)
        booking = bookings[1]
        booking.date, booking.time = performance_date.date(), performance_date.time()
        booking.save()

        disable_ratings_for_old_bookings()

        bookings = Booking.objects.all().order_by('pk')

        self.assertTrue(bookings[0].can_be_rated)
        self.assertFalse(bookings[1].can_be_rated)


class BookingTransactionTestCase(AuthClientTestCase):

    @staticmethod
    def __confirm_payment_intent(payment_intent: str):
        PaymentService().provider.PaymentIntent.confirm(payment_intent, payment_method='pm_card_visa')

    def test_transactions_booking_payment_by_booker(self):
        booking = self.test_data_service.create_random_bookings(count=1, paid=True)[0]
        self.__confirm_payment_intent(payment_intent=booking.payment_intent_id)
        PaymentService().pay_booking(booking.payment_intent_id, amount=int(booking.get_price() * 100))
        transactions = Transaction.objects.filter(entity_pk=booking.id).order_by('id')

        self.assertEqual(transactions[0].purpose, TransactionPurposes.PAYMENT)
        self.assertEqual(transactions[0].amount, int(booking.get_price() * 100))

        self.assertEqual(transactions[1].purpose, TransactionPurposes.BOOKING_ESCROW)
        booking_escrow_value = -1 * (PaymentService._convert_amount_to_cents(booking.get_price()) - PaymentService._convert_amount_to_cents(booking.booker_fee))
        self.assertEqual(transactions[1].amount, booking_escrow_value)

        self.assertEqual(transactions[2].purpose, TransactionPurposes.BOOKING_FEE_FOR_BOOKER)
        self.assertEqual(transactions[2].amount, int(-1 * booking.booker_fee * 100))

    def test_transactions_booking_decline_by_dj(self):
        data = {'decline_comment': DECLINE_COMMENT}
        booking = self.test_data_service.get_random_booking_by_account(
            account=self.dj_user.get_account(),
            count=5
        )
        self.__confirm_payment_intent(payment_intent=booking.payment_intent_id)
        PaymentService().pay_booking(booking.payment_intent_id,
                                     amount=int(booking.get_price() * 100))
        response = self.dj_client.post(
            reverse('booking:booking_decline', args=[booking.pk]), data=data)
        transactions = Transaction.objects.filter(entity_pk=booking.id).order_by('id')
        # TODO: починить, иногда не проходит refund по времени
        self.assertEqual(len(list(transactions)), 4)
        self.assertEqual(transactions[3].purpose, TransactionPurposes.REFUND)
        self.assertEqual(transactions[3].amount, int(booking.get_price() * 100))

    def test_transactions_booking_decline_by_booker(self):
        data = {'decline_comment': DECLINE_COMMENT}
        booking = self.test_data_service.get_random_booking_by_account(
            account=self.booker_user.get_account(),
            count=5
        )
        self.__confirm_payment_intent(payment_intent=booking.payment_intent_id)
        PaymentService().pay_booking(booking.payment_intent_id,
                                     amount=int(booking.get_price() * 100))
        response = self.booker_client.post(
            reverse('booking:booking_decline', args=[booking.pk]), data=data)
        transactions = Transaction.objects.filter(entity_pk=booking.id).order_by('id')
        # TODO: починить, иногда не проходит refund по времени
        self.assertEqual(len(list(transactions)), 4)
        self.assertEqual(transactions[3].purpose, TransactionPurposes.REFUND)
        self.assertEqual(transactions[3].amount, int(booking.get_price() * 100))

    def test_transactions_transfer_money_to_dj(self):
        booking = self.test_data_service.create_random_bookings(count=1, paid=True)[0]
        self.__confirm_payment_intent(payment_intent=booking.payment_intent_id)
        PaymentService().pay_booking(booking.payment_intent_id,
                                     amount=int(booking.get_price() * 100))
        booking.success()
        PaymentService().transfer_money_for_dj(booking=booking)

        transactions = Transaction.objects.filter(entity_pk=booking.id).order_by('id')

        self.assertEqual(transactions[3].purpose, TransactionPurposes.PAYMENT_TO_USER)
        self.assertEqual(transactions[3].amount, PaymentService._convert_amount_to_cents(
            booking.get_price()) - PaymentService._convert_amount_to_cents(booking.booker_fee)
        )
        self.assertEqual(transactions[4].purpose, TransactionPurposes.BOOKING_FEE_FOR_DJ)
        self.assertEqual(transactions[4].amount, -1 * PaymentService._convert_amount_to_cents(booking.dj_fee))
