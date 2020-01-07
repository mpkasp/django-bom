NO_CHOICE = ('', '-----')

SUBSCRIPTION_TYPES = (('F', 'Free'), ('P', 'Pro'),)
ROLE_TYPES = (('A', 'Admin'), ('V', 'Viewer'),)
CONFIGURATION_TYPES = (('R', 'Released'), ('W', 'Working'),)

DATA_SOURCES = (
    NO_CHOICE,
    ('octopart', 'octopart'),
    ('mouser', 'mouser'),
)

VALUE_UNITS = (
    NO_CHOICE,
    ('Ohms', '\u03A9'),
    ('mOhms', 'm\u03A9'),
    ('kOhms', 'k\u03A9'),
    ('F', 'F'),
    ('pF', 'pF'),
    ('nF', 'nF'),
    ('uF', '\u03BCF'),
    ('V', 'V'),
    ('uV', '\u03BCV'),
    ('mV', 'mV'),
    ('A', 'A'),
    ('uA', '\u03BCA'),
    ('mA', 'mA'),
    ('C', '\u00B0C'),
    ('F', '\u00B0F'),
    ('H', 'H'),
    ('mH', 'mH'),
    ('uH', '\u03BCH'),
    ('Hz', 'Hz'),
    ('kHz', 'kHz'),
    ('MHz', 'MHz'),
    ('GHz', 'GHz'),
    ('Other', 'Other'),
)

PACKAGE_TYPES = (
    NO_CHOICE,
    ('0201 smd', '0201 smd'),
    ('0402 smd', '0402 smd'),
    ('0603 smd', '0603 smd'),
    ('0805 smd', '0805 smd'),
    ('1206 smd', '1206 smd'),
    ('1210 smd', '1210 smd'),
    ('1812 smd', '1812 smd'),
    ('2010 smd', '2010 smd'),
    ('2512 smd', '2512 smd'),
    ('1/8 radial', '1/8 radial'),
    ('1/4 radial', '1/4 radial'),
    ('1/2 radial', '1/2 radial'),
    ('Size A', 'Size A'),
    ('Size B', 'Size B'),
    ('Size C', 'Size C'),
    ('Size D', 'Size D'),
    ('Size E', 'Size E'),
    ('SOT-23', 'SOT-23'),
    ('SOT-223', 'SOT-233'),
    ('DIL', 'DIL'),
    ('SOP', 'SOP'),
    ('SOIC', 'SOIC'),
    ('QFN', 'QFN'),
    ('QFP', 'QFP'),
    ('QFT', 'QFT'),
    ('PLCC', 'PLCC'),
    ('VGA', 'VGA'),
    ('Other', 'Other'),
)

DISTANCE_UNITS = (
    NO_CHOICE,
    ('mil', 'mil'),
    ('in', 'in'),
    ('ft', 'ft'),
    ('yd', 'yd'),
    ('km', 'km'),
    ('m', 'm'),
    ('cm', 'cm'),
    ('mm', 'mm'),
    ('um', '\u03BCm'),
    ('nm', 'nm'),
    ('Other', 'Other'),
)

WEIGHT_UNITS = (
    NO_CHOICE,
    ('mg', 'mg'),
    ('g', 'g'),
    ('kg', 'kg'),
    ('oz', 'oz'),
    ('lb', 'lb'),
    ('Other', 'Other'),
)

TEMPERATURE_UNITS = (
    NO_CHOICE,
    ('C', '\u00B0C'),
    ('F', '\u00B0F'),
    ('Other', 'Other'),
)

WAVELENGTH_UNITS = (
    NO_CHOICE,
    ('km', 'km'),
    ('m', 'm'),
    ('cm', 'cm'),
    ('mm', 'mm'),
    ('um', '\u03BCm'),
    ('nm', 'nm'),
    ('A', '\u212B'),
    ('Other', 'Other'),
)

FREQUENCY_UNITS = (
    NO_CHOICE,
    ('Hz', 'Hz'),
    ('kHz', 'kHz'),
    ('MHz', 'MHz'),
    ('GHz', 'GHz'),
    ('Other', 'Other'),
)

MEMORY_UNITS = (
    NO_CHOICE,
    ('KB', 'KB'),
    ('MB', 'MB'),
    ('GB', 'GB'),
    ('TB', 'TB'),
    ('Other', 'Other'),
)

INTERFACE_TYPES = (
    NO_CHOICE,
    ('I2C', 'I2C'),
    ('SPI', 'SPI'),
    ('CAN', 'CAN'),
    ('One-Wire', '1-Wire'),
    ('RS485', 'RS-485'),
    ('RS232', 'RS-232'),
    ('WiFi', 'Wi-Fi'),
    ('4G', '4G'),
    ('BT', 'Bluetooth'),
    ('BTLE', 'Bluetooth LE'),
    ('Z_Wave', 'Z-Wave'),
    ('Zigbee', 'Zigbee'),
    ('LAN', 'LAN'),
    ('USB', 'USB'),
    ('HDMI', 'HDMI'),
    ('Other', 'Other'),
)

POWER_UNITS = (
    NO_CHOICE,
    ('W', 'W'),
    ('uW', '\u03BCW'),
    ('mW', 'mW'),
    ('kW', 'kW'),
    ('MW', 'MW'),
    ('Other', 'Other'),
)

VOLTAGE_UNITS = (
    NO_CHOICE,
    ('V', 'V'),
    ('uV', '\u03BCV'),
    ('mV', 'mV'),
    ('kV', 'kV'),
    ('MV', 'MV'),
    ('Other', 'Other'),
)

CURRENT_UNITS = (
    NO_CHOICE,
    ('A', 'A'),
    ('uA', '\u03BCV'),
    ('mA', 'mA'),
    ('kA', 'kA'),
    ('MA', 'MA'),
    ('Other', 'Other'),
)
