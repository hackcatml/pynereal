from ..core.callable_module import CallableModule
from ..types.color import Color
from ..types.na import NA


class BgColorModule(CallableModule):
    def __call__(
        self,
        color: Color | str | NA | None,
        offset: int = 0,
        editable: bool = True,
        show_last: int | None = None,
        title: str | None = None,
        force_overlay: bool = False,
        *args,
        **kwargs,
    ) -> None: ...


bgcolor: BgColorModule = BgColorModule(__name__)
