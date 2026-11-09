from django.core.management.base import BaseCommand

from apps.bookings.services import expire_stale_bookings, expire_stale_notifications


class Command(BaseCommand):
    help = (
        "Отменяет неподтверждённые брони (LIBRARY.BOOKING_HOLD_HOURS) и закрывает "
        "просроченные уведомления очереди (LIBRARY.QUEUE_NOTIFY_HOURS). Запускать по cron."
    )

    def handle(self, *args, **options):
        bookings = expire_stale_bookings()
        queue = expire_stale_notifications()
        self.stdout.write(self.style.SUCCESS(f"Отменено броней: {bookings}; истекло уведомлений очереди: {queue}"))
