import os

from booking.models import Booking
from file_storage.services import FileStorageService
from users.enums import GigTypes, Music


class BookingService:

    @staticmethod
    def create_booking_report_file(booking) -> str:
        """ Create booking info file and return file name"""

        from booking.serializers import BookingSerializer

        data = BookingSerializer(booking).data
        music_list = ', '.join([str(Music(i).label) for i in booking.music_list])
        listeners = ', '.join([str(Booking.Listener(i).label) for i in booking.listeners])
        requirements = {
            'Clean Mix': data['clean_mix'],
            'Virtual Mix': data['virtual_mix']
        }
        requested_equipment = {
            'Microphone': data['add_ons_microphone'],
            'Fog Machine': data['add_ons_fog_machine'],
            'Power Cord': data['add_ons_power_cords']
        }
        context = {
            'state': data['location_state'],
            'city': data['location_city'],
            'booker_profile': data['booker_profile'],
            'dj_profile': data['dj_profile'],
            'booker': data['booker_profile']['user'],
            'dj': data['dj_profile']['user'],
            'date': data['date'],
            'time': data['time'],
            'duration_in_hours': data['duration_in_hours'],
            'total_cost': data['total_cost'],
            'table_data': {
                'Music': music_list,
                'Gig Type': str(GigTypes(booking.gig_type).label),
                'Expected Listeners Age': listeners,
                'Outdoor Event Location:': Booking.OUT_DOOR_PLAYS[booking.out_door_play][1],
                'Event Space:': Booking.IN_DOOR_PLAYS[booking.out_door_play][1],
                'Note to Performer': booking.comment
            },
            'requirements': [key for key, value in requirements.items() if value is True],
            'requested_addons': [key for key, value in requested_equipment.items() if value is True]
        }
        file_path = FileStorageService().create_pdf_file(
            template_path='bookings/report/template.html',
            data=context
        )
        file_name = os.path.basename(file_path)
        return file_name
