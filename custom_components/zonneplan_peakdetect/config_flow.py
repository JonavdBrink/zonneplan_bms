from homeassistant import config_entries
from .const import DOMAIN

class ZonneplanPeakDetectConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Zonneplan Peak Detect."""

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="Zonneplan Peak Detect", data={})
        return self.async_show_form(step_id="user")