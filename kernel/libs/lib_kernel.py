def get_duration(duration):
        hours = int(duration / 3600)
        minutes = int(duration % 3600 / 60)
        seconds = int((duration % 3600) % 60)
        return '{:02d}:{:02d}:{:02d}'.format(hours, minutes, seconds)