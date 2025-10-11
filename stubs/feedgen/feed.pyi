# Stub file for feedgen.feed module
from typing import Any, Optional

class FeedGenerator:
    def title(self, title: Optional[str] = None) -> Optional[str]: ...
    def description(self, description: Optional[str] = None) -> Optional[str]: ...
    def link(
        self, link: Optional[str] = None, replace: bool = False, **kwargs: Any
    ) -> Optional[list[Any]]: ...
    def add_entry(
        self, feedEntry: Optional[Any] = None, order: str = "prepend"
    ) -> Any: ...
    def rss_file(
        self,
        filename: str,
        extensions: bool = True,
        pretty: bool = False,
        encoding: str = "UTF-8",
        xml_declaration: bool = True,
    ) -> None: ...

class FeedEntry:
    def title(self, title: Optional[str] = None) -> Optional[str]: ...
    def description(
        self, description: Optional[str] = None, isSummary: bool = False
    ) -> Optional[str]: ...
    def link(
        self, link: Optional[str] = None, replace: bool = False, **kwargs: Any
    ) -> Optional[list[Any]]: ...
    def enclosure(
        self,
        url: Optional[str] = None,
        length: Optional[str] = None,
        type: Optional[str] = None,
    ) -> Optional[dict[str, Any]]: ...
    def id(self, id: Optional[str] = None) -> Optional[str]: ...
