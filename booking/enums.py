from django.db import models
from django.utils.translation import gettext_lazy as _


class TransactionPurposes(models.IntegerChoices):

    PAYMENT = 1, _('Money deposit to the balance')
    BOOKING_ESCROW = 2, _('Escrow for Booking')
    BOOKING_FEE_FOR_BOOKER = 3, _('Fee for Booking')
    PAYMENT_TO_USER = 4, _('Payment to performer for Booking')
    BOOKING_FEE_FOR_DJ = 5, _('Fee for performer')
    REFUND = 6, _('Refund of money to the balance')
    WITHDRAWAL = 7, _('Withdrawal of money from system')

    @classmethod
    def get_keys(cls):
        return [key.name for key in cls]

    @staticmethod
    def get_transaction_list():
        return [
            TransactionPurposes.PAYMENT,
            TransactionPurposes.BOOKING_ESCROW,
            TransactionPurposes.BOOKING_FEE_FOR_BOOKER,
            TransactionPurposes.PAYMENT_TO_USER,
            TransactionPurposes.PAYMENT_FEE_FOR_DJ,
            TransactionPurposes.REFUND,
            TransactionPurposes.WITHDRAWAL
        ]


class PromocodeType(models.IntegerChoices):
    FIXED = 1, _('Fixed amount')
    PERCENT = 2, _('Percent')
