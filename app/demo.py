"""Demo-Umgebung: taeglicher Reset (und spaeter die Startdaten).

Das Modul ist ohne Nebenwirkungen importierbar; create_app() importiert es
erst im Demo-Modus, um einen Zirkelimport zu vermeiden.
"""
import logging
import os
import signal

logger = logging.getLogger(__name__)


def demo_reset() -> None:
    """Beendet den Gunicorn-Master, damit Docker den Container neu startet.

    Nur im Container (KASSENSTUERZLE_IM_CONTAINER=1, steht im Dockerfile) -
    bewusst nicht ueber /.dockerenv, damit ein Dev-Container nie getroffen
    wird. Sonst nur eine Warnung: der Job darf nie die Shell eines
    Entwicklers beenden.
    """
    if os.environ.get('KASSENSTUERZLE_IM_CONTAINER') != '1':
        logger.warning('Demo-Reset uebersprungen: Prozess laeuft nicht im '
                       'Container (KASSENSTUERZLE_IM_CONTAINER != 1).')
        return
    master = os.getppid()
    logger.info('Demo-Reset: sende SIGTERM an den Gunicorn-Master (%s).', master)
    os.kill(master, signal.SIGTERM)


def schedule_demo_reset(scheduler, hour: int, minute: int) -> None:
    """Traegt den taeglichen Reset-Job ein (lokale Zeitzone, siehe TZ)."""
    scheduler.add_job(
        demo_reset, 'cron', hour=hour, minute=minute,
        id='demo_reset', replace_existing=True,
    )
