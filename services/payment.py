import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

import stripe
from booking.models import Booking, Transaction, TransactionPurposes, Withdrawal
from chat.services import ChatRoomService
from notifications.service import NotifyService
from utils.email import (send_withdrawal_confirmation_to_booker,
                         send_withdrawal_confirmation_to_dj)
from utils.loggers import current_func_name

logger = logging.getLogger('django')


class PaymentService:
    DAYS_FOR_FULL_REFUND = 2
    HOURS_FOR_PARTIAL_REFUND = 12
    PERCENT_MORE_12_HOURS = 70
    PERCENT_LESS_12_HOURS = 50
    provider = stripe

    def __init__(self):
        self.provider.api_key = settings.STRIPE_SECRET_KEY

    @staticmethod
    def _convert_amount_to_cents(amount: float):
        return int(amount * 100)

    @staticmethod
    def _convert_amount_to_dollars(amount: float):
        return amount / 100

    def __is_conditions_for_full_refund(self, booking, account):
        return account == booking.account_dj or timezone.now() + timedelta(
            days=self.DAYS_FOR_FULL_REFUND) <= booking.get_datetime()

    def create_account(self, email):
        logger.debug(f"{__class__.__name__}.{current_func_name()}")
        res = self.provider.Account.create(
            type='custom',
            country="US",
            email=email,
            business_type='individual',
            capabilities={
                "card_payments": {"requested": True},
                "transfers": {"requested": True},
            },
            settings={
                'payouts': {
                    'schedule': {
                        'interval': 'manual',
                    },
                },
            }
        )
        return res

    def create_account_link(self, stripe_account: str, account_type: str):
        logger.debug(f"{__class__.__name__}.{current_func_name()}")
        return self.provider.AccountLink.create(
            account=stripe_account,
            refresh_url=f'{settings.BASE_CLIENT_URL}/profile/{account_type}/wallet?renew=Y',
            return_url=f'{settings.BASE_CLIENT_URL}/profile/{account_type}/wallet?return=Y',
            type='account_onboarding',
            collect='eventually_due',
        )['url']

    def create_payment_intent(self, booking, amount):
        logger.debug(f"{__class__.__name__}.{current_func_name()}")
        return self.provider.PaymentIntent.create(
            amount=self._convert_amount_to_cents(amount),
            currency='usd',
            metadata={
                'booking_id': booking.pk
            }
        )

    def connect_card_to_stripe(self, user, card_token: str):
        logger.debug(f"{__class__.__name__}.{current_func_name()}")

        data = self.provider.Account.create_external_account(
            user.stripe_account,
            external_account=card_token
        )

        card_visual_info = f'**** **** **** {data["last4"]} Expiration: {data["exp_month"]}/{data["exp_year"]}'

        if not user.payment_cards:
            user.payment_cards = {data['id']: card_visual_info}
        else:
            user.payment_cards[data['id']] = card_visual_info

        user.save()

        return data

    def list_cards_connected_to_stripe(self, stripe_account: str, limit: int = None, starting_after: int = None):
        logger.debug(f"{__class__.__name__}.{current_func_name()}")
        kwargs = {}
        if limit is not None:
            kwargs['limit'] = limit
        if starting_after is not None:
            kwargs['starting_after'] = starting_after

        return self.provider.Account.list_external_accounts(
            stripe_account,
            object="card",
            **kwargs
        )

    def delete_card_from_stripe(self, user, destination_card: str):
        logger.debug(f"{__class__.__name__}.{current_func_name()}")

        res = self.provider.Account.delete_external_account(
            user.stripe_account, destination_card)

        user.payment_cards.pop(destination_card, None)
        user.save()

        return res

    def set_card_as_default_for_currency(self, user, destination_card: str):
        logger.debug(f"{__class__.__name__}.{current_func_name()}")

        return stripe.Account.modify_external_account(
            user.stripe_account,
            destination_card,
            default_for_currency=True
        )

    def get_account(self, stripe_account: str):
        logger.debug(f"{__class__.__name__}.{current_func_name()}")
        return self.provider.Account.retrieve(stripe_account=stripe_account)

    @transaction.atomic
    def pay_booking(self, payment_intent_id: str, amount: int):
        """
        Callback to make payments in the system after Stripe webhook is called
        """
        logger.debug(f"{__class__.__name__}.{current_func_name()}")
        try:
            booking = Booking.objects.get(payment_intent_id=payment_intent_id)
        except Booking.DoesNotExist:
            pass
        else:
            Transaction.objects.create_transaction(
                amount=amount,
                user=booking.account_booker.user,
                purpose=TransactionPurposes.PAYMENT,
                entity=booking
            )
            Transaction.objects.create_transaction(
                amount=Transaction.objects.convert_to_negative_value(
                    self._convert_amount_to_cents(booking.get_price()) - self._convert_amount_to_cents(booking.booker_fee)
                ),
                user=booking.account_booker.user,
                purpose=TransactionPurposes.BOOKING_ESCROW,
                entity=booking
            )
            Transaction.objects.create_transaction(
                amount=Transaction.objects.convert_to_negative_value(
                    self._convert_amount_to_cents(booking.booker_fee)
                ),
                user=booking.account_booker.user,
                purpose=TransactionPurposes.BOOKING_FEE_FOR_BOOKER,
                entity=booking
            )
            booking.pay()
            ChatRoomService().create_chat_room(booking=booking)

    @transaction.atomic
    def pay_booking_from_balance(self, booking):
        logger.debug(f"{__class__.__name__}.{current_func_name()}")

        Transaction.objects.create_transaction(
            amount=Transaction.objects.convert_to_negative_value(
                self._convert_amount_to_cents(booking.get_price()) - self._convert_amount_to_cents(booking.booker_fee)
            ),
            user=booking.account_booker.user,
            purpose=TransactionPurposes.BOOKING_ESCROW,
            entity=booking
        )
        Transaction.objects.create_transaction(
            amount=Transaction.objects.convert_to_negative_value(
                self._convert_amount_to_cents(booking.booker_fee)
            ),
            user=booking.account_booker.user,
            purpose=TransactionPurposes.BOOKING_FEE_FOR_BOOKER,
            entity=booking
        )
        booking.pay()
        ChatRoomService().create_chat_room(booking=booking)

    def cancel_booking_payment(self, booking) -> bool:
        logger.debug(f"{__class__.__name__}.{current_func_name()}")
        logger.info(f'{booking}, payment_intent_id {booking.payment_intent_id}')

        if booking.payment_intent_id:
            try:
                canceled_payment_intent = self.provider.PaymentIntent.cancel(
                    booking.payment_intent_id
                )
            except stripe.error.InvalidRequestError as e:
                logger.exception(e)
                booking.delete()
                return
            else:
                logger.info(f'status: {canceled_payment_intent["status"]}')
                booking.delete()
                return canceled_payment_intent['status'] == 'canceled'

        booking.delete()

    @transaction.atomic
    def transfer_money_for_dj(self, booking):
        logger.debug(f"{__class__.__name__}.{current_func_name()}")
        booking.completed()
        Transaction.objects.create_transaction(
            amount=self._convert_amount_to_cents(booking.get_price()) - self._convert_amount_to_cents(booking.booker_fee),
            user=booking.account_dj.user,
            purpose=TransactionPurposes.PAYMENT_TO_USER,
            entity=booking
        )
        Transaction.objects.create_transaction(
            amount=Transaction.objects.convert_to_negative_value(
                self._convert_amount_to_cents(booking.dj_fee)
            ),
            user=booking.account_dj.user,
            purpose=TransactionPurposes.BOOKING_FEE_FOR_DJ,
            entity=booking
        )

    def refund_money_for_booker(self, booking, refund_percent: int = 100):
        logger.debug(f"{__class__.__name__}.{current_func_name()}")
        Transaction.objects.create_transaction(
            amount=self._convert_amount_to_cents(booking.get_price()),
            user=booking.account_booker.user,
            purpose=TransactionPurposes.REFUND,
            entity=booking
        )

    def refund_booking(self, booking, account):
        logger.debug(f"{__class__.__name__}.{current_func_name()}")
        if self.__is_conditions_for_full_refund(booking=booking, account=account):
            self.refund_money_for_booker(booking=booking)
        elif timezone.now() + timedelta(hours=self.HOURS_FOR_PARTIAL_REFUND) <= booking.get_datetime():
            self.refund_money_for_booker(booking=booking, refund_percent=self.PERCENT_MORE_12_HOURS)
        else:
            self.refund_money_for_booker(booking=booking, refund_percent=self.PERCENT_LESS_12_HOURS)

    def transfer_money_to_stripe_account(self, withdrawal: Withdrawal):
        logger.debug(f"{__class__.__name__}.{current_func_name()}")
        return self.provider.Transfer.create(
            amount=withdrawal.amount,
            currency="usd",
            destination=withdrawal.user.stripe_account,
            metadata={
                'withdrawal_id': withdrawal.pk
            }
        )

    @transaction.atomic
    def withdraw(self, withdrawal_id: str):
        logger.debug(f"{__class__.__name__}.{current_func_name()}")
        try:
            withdrawal = Withdrawal.objects.get(pk=withdrawal_id)
        except Withdrawal.DoesNotExist:
            raise ValueError(f'Incorrect withdrawal_id: {withdrawal_id}')
        else:
            Transaction.objects.create_transaction(
                amount=withdrawal.amount,
                user=withdrawal.user,
                purpose=TransactionPurposes.WITHDRAWAL,
                entity=withdrawal
            )
            return withdrawal

    def payout_to_card(self, withdrawal: Withdrawal):
        logger.debug(f"{__class__.__name__}.{current_func_name()}")
        from users.models import Account
        try:
            res = self.provider.Payout.create(
                amount=withdrawal.amount,
                currency='usd',
                stripe_account=withdrawal.user.stripe_account,
                destination=withdrawal.destination_card
                # method='instant'  # TODO: подойдет не для всех карт, пока не буду делать явно
            )
        except Exception as e:
            withdrawal.result = str(e)
            withdrawal.save()
            raise e
        else:
            withdrawal.result = 'Success'
            withdrawal.save()

            accounts = withdrawal.user.get_accounts()
            dj_account = accounts.filter(dj_profile__isnull=False).first()
            booker_account = accounts.filter(booker_profile__isnull=False).first()
            if dj_account is not None:
                send_withdrawal_confirmation_to_dj(
                    [withdrawal.user.email],
                    user=withdrawal.user
                )
                NotifyService().create_notify_withdrawal_confirmation(
                    account=dj_account,
                    withdrawal=withdrawal
                )
            else:
                send_withdrawal_confirmation_to_booker(
                    [withdrawal.user.email],
                    user=withdrawal.user
                )
                NotifyService().create_notify_withdrawal_confirmation(
                    account=booker_account,
                    withdrawal=withdrawal
                )
            return res
