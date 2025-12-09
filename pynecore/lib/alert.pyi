from ..core.callable_module import CallableModule
from ..types.alert import AlertEnum


class AlertModule(CallableModule):
    freq_all = AlertEnum()
    freq_once_per_bar = AlertEnum()
    freq_once_per_bar_close = AlertEnum()

    def alert(
            self,
            message: str,
            freq: AlertEnum = AlertModule.freq_once_per_bar
    ) -> None:
        ...


alert: AlertModule = AlertModule(__name__)