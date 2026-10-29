# Wireless Strain Gauge Dashboard

Dit dashboard is gemaakt voor het Wireless Strain Gauge Data Transceiver-project
bij Huisman. Hiermee kunnen we de meetwaarden van drie strain gauges bekijken,
de nodes starten en stoppen en de ontvangen data opslaan.

Het programma is geschreven in Python met Tkinter voor de interface en pyserial
voor de seriële verbinding.

## Installeren en starten

Je hebt Python 3.10 of nieuwer nodig, met Tkinter. Tkinter zit normaal al bij de
Windows-installatie van Python. Installeer daarnaast pyserial:

```sh
python -m pip install pyserial
```

Start daarna het dashboard vanuit de map `dashboard`:

```sh
python wireless_strain_gauge_data_transceiver_dashboard.py
```

Op Windows kun je ook `py` gebruiken in plaats van `python`.

## Gebruik

Selecteer de COM-poort van de receiver en klik op **Connect**. Met **Scan** kun je
de lijst met poorten vernieuwen. De verbinding werkt op 115200 baud.
Met **Start node** en **Stop node** stuur je een start- of stopcommando naar de
receiver.

De eerste drie ontvangen gauge-ID's krijgen elk een eigen grafiek. Het ID staat
boven de grafiek, samen met de actuele meetwaarde, ruwe waarde en pakketgegevens.
De tijdas gebruikt de sampletijd van de transmitter. Bij **Graph settings** kun
je de tijdsduur en het automatische of handmatige Y-bereik instellen.

De instellingen rechts zijn scrollbaar. Het venster kan worden vergroot of
verkleind, met een minimum van 1120 × 720 bij 100% schermschaal.

## Kalibratie

Elke gauge heeft een eigen tabblad met een offset, gain en eenheid. De berekening is:

```text
meetwaarde = (raw - zero) × gain + offset
```

Met **Apply** pas je de ingevulde waarden toe. **Zero balance** gebruikt de laatste
sample als nulpunt en zet de offset op nul. Met **Reset calibration** zet je de
kalibratie terug naar de standaardwaarden.

**Clear graphs** wist de meetgeschiedenis en begint de tijdas opnieuw. De
kalibratie en de toegewezen gauge-ID's blijven bewaard.

## Data opslaan

Meetdata en debugberichten kunnen apart worden opgeslagen als CSV. Kies hiervoor
**Start data CSV** of **Start debug CSV** en geef aan waar het bestand moet komen.
Tijdens het loggen staat er “Recording” op de knop. Klik nogmaals om te stoppen.
De tijdstempels van de pc worden opgeslagen in UTC.

Bij **Live output** staan de ontvangen data en debugberichten in aparte tabbladen.
De knoppen **Clear data** en **Clear debug** wissen alleen de terminalweergave,
niet de CSV-bestanden.

## Bestanden

- `wireless_strain_gauge_data_transceiver_dashboard.py`: het dashboard.
- `TX_simulator.py`: simulator om zonder aangesloten transmitter te testen.
- `tests/test_dashboard.py`: automatische tests voor onder andere de verwerking
  van pakketten, kalibratie, commando's en logging.
- `COM_Communication_Protocol.md`: uitleg van het seriële protocol.
- `huisman_logo.png`: het logo dat in de header wordt gebruikt.

## Testen

Voer de tests uit vanuit de map `dashboard`:

```sh
python -m unittest discover -s tests -v
```

De tests maken echte Tkinter-widgets aan en hebben daarom een grafische
bureaubladsessie nodig. Voor uitleg over de simulator kun je dit commando gebruiken:

```sh
python TX_simulator.py --help
```

De automatische tests vervangen het testen met echte hardware niet. Controleer
op de Windows-pc ook de COM-verbinding en de weergave bij de gebruikte schermschaal.
