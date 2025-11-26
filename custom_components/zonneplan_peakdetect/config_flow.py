import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import CONF_PERCENTAGE, CONF_CENTS, DOMAIN, DEFAULT_PERCENTAGE, DEFAULT_CENTS

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Behandelt een config flow voor jouw integratie."""

    VERSION = 1
    async def async_step_user(self, user_input=None):
        """Behandelt de initiële stap."""
        errors = {}

        if user_input is not None:
            # De configuratie opslaan
            return self.async_create_entry(
                title="Sensor settings", 
                data=user_input
            )

        # Schema voor het formulier dat de gebruiker te zien krijgt
        data_schema = vol.Schema({
            # Percentage: type float, met een standaardwaarde
            vol.Required(
                CONF_PERCENTAGE, 
                default=DEFAULT_PERCENTAGE
            ): float,
            
            # Bedrag in Centen: type integer (centen zijn gehele getallen), met een standaardwaarde
            vol.Required(
                CONF_CENTS,
                default=DEFAULT_CENTS
            ): int,
        })

        return self.async_show_form(
            step_id="user", 
            data_schema=data_schema, 
            errors=errors
        )

# class ZonneplanPeakDetectConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
#     """Handle a config flow for Zonneplan Peak Detect."""

#     async def async_step_user(self, user_input=None):
#         if user_input is not None:
#             return self.async_create_entry(title="Zonneplan Peak Detect", data={})
#         return self.async_show_form(step_id="user")