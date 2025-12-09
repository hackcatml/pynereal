from datetime import datetime, timedelta

from rich.text import Text
from rich.progress import ProgressColumn


class DateColumn(ProgressColumn):
    """ Custom progress column showing current date being processed. """

    def __init__(self, start_time: datetime):
        super().__init__()
        self.start_time = start_time

    def render(self, task) -> Text:
        # Calculate current date from progress
        current_date = self.start_time + timedelta(seconds=task.completed)
        return Text(current_date.strftime("%Y-%m-%d %H:%M"), style="magenta")
