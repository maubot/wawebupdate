# wawebupdate - A maubot plugin to detect WhatsApp web updates and notify a room about them.
# Copyright (C) 2022 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
from __future__ import annotations

from typing import Type
import asyncio

from yarl import URL
from semver import VersionInfo

from maubot import Plugin
from mautrix.errors import MForbidden
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper


FX_VERSION = 102
USER_AGENT = (
    f"Mozilla/5.0 (X11; Linux x86_64; rv:{FX_VERSION}.0) Gecko/20100101 Firefox/{FX_VERSION}.0"
)


class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("last_known_version")
        helper.copy("platform")


class WAWebUpdateBot(Plugin):
    poll_task: asyncio.Task
    url = URL("https://web.whatsapp.com/check-update")

    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config

    async def start(self) -> None:
        self.config.load_and_update()
        self.poll_task = asyncio.create_task(self.poll())

    async def stop(self) -> None:
        self.poll_task.cancel()

    @property
    def platform(self) -> str:
        return self.config["platform"]

    @property
    def last_known_version(self) -> VersionInfo | None:
        ver = self.config["last_known_version"]
        return VersionInfo.parse(ver) if ver else None

    @last_known_version.setter
    def last_known_version(self, val: VersionInfo) -> None:
        self.config["last_known_version"] = str(val)
        self.config.save()

    async def poll(self) -> None:
        while True:
            try:
                await self._poll_once()
            except Exception:
                self.log.exception("Error polling WhatsApp web version")
            await asyncio.sleep(60 * 60)

    async def _poll_once(self) -> None:
        check_url = self.url.with_query({
            "version": str(self.last_known_version) or "2.2222.11",
            "platform": self.platform,
        })
        resp = await self.http.get(check_url, headers={
            "User-Agent": USER_AGENT
        })
        resp_data = await resp.json(content_type=None)
        current_version = VersionInfo.parse(resp_data["currentVersion"])
        if self.last_known_version is None or current_version != self.last_known_version:
            if self.last_known_version is not None:
                try:
                    await self._notify_change(check_url, self.last_known_version, current_version)
                except Exception:
                    self.log.exception("Error notifying rooms about WhatsApp web update")
            self.last_known_version = current_version

    async def _notify_change(
        self, url: URL, old_version: VersionInfo, new_version: VersionInfo
    ) -> None:
        thing = {
            "web": "web.whatsapp.com",
            "darwin": "WhatsApp macOS",
            "darwin-beta": "WhatsApp macOS (beta)",
            "win32": "WhatsApp Windows",
            "win32-beta": "WhatsApp Windows (beta)",
            "win32-store": "WhatsApp Windows (store)",
        }.get(self.platform, "unknown WhatsApp web platform")
        action = "updated" if new_version > old_version else "downgraded"
        emoji = "🎉" if new_version > old_version else "🤔"
        msg = f"{thing} has {action} [from {old_version}]({url}) to {new_version} {emoji}"
        self.log.info(f"{thing} has {action} from {old_version} to {new_version}")
        for room_id in await self.client.get_joined_rooms():
            try:
                await self.client.send_markdown(room_id, msg)
            except MForbidden as e:
                self.log.warning(f"Failed to notify {room_id} about update: {e}, leaving room")
                try:
                    await self.client.leave_room(room_id)
                except Exception:
                    self.log.exception(f"Failed to leave {room_id} after error sending message")
            except Exception:
                self.log.exception(f"Failed to notify {room_id} about update")
