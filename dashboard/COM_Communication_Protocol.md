# COM Communication Protocol

## Seriële verbinding en meetbericht

Eén seriële verbinding: **115200 baud, 8 databits, geen pariteit, 1 stopbit**,
geen flow control. ASCII-tekstregels worden afgesloten met CRLF (`\r\n`).

```text
LC,<strain_gauge_id>,<sequence>,<raw_value>,<flags>,<tx_sample_ms>
```

Het tweede veld is een unieke **strain_gauge_id**, geen protocolnummer.
Een oud bericht `LC,2,...` wordt geïnterpreteerd als gauge met ID `2`.
Firmware en simulator moeten dezelfde nieuwe veldbetekenis gebruiken.

| Veld | Betekenis |
| --- | --- |
| `LC` | Vast voorvoegsel voor meetdata. |
| `strain_gauge_id` | Hoofdlettergevoelige ID van 1–32 ASCII-letters, cijfers, underscores of koppeltekens. Uniek en stabiel per gauge. |
| `sequence` | Volgnummer per gauge, unsigned 16-bit: 0–65535, daarna 0. |
| `raw_value` | Ruwe meetwaarde als decimaal geheel getal; negatief toegestaan. |
| `flags` | Niet-negatieve gehele statuswaarde, hexadecimaal weergegeven. Betekenis van de bits wordt door firmware bepaald. |
| `tx_sample_ms` | Sampletijd van de betreffende transmitter, unsigned 32-bit milliseconden: 0–4294967295. |

Voorbeeld van drie door elkaar verzonden datastromen:

```text
LC,SG_1,1534,82731,0,1234567
LC,SG_2,81,86420,0,8100
LC,SG_3,327,79800,0,32700
LC,SG_1,1535,82744,0,1234667
```

Het dashboard wijst de eerste drie unieke ID's automatisch toe aan drie
tegelijk zichtbare grafieken. Een vierde ID wordt genegeerd en als debug
geregistreerd. Herstart het dashboard om een andere set ID's toe te wijzen.

Elke gauge heeft een eigen meetbuffer (maximaal 900 samples), sequencecontrole,
samplefrequentie en tijdsbasis. Grafieken tonen tijd ten opzichte van de nieuwste
sample van hun eigen gauge; dit synchroniseert de transmitters niet.
Bij een teruglopende timestamp groter dan 2^31 ms wordt uint32-overloop
verwerkt. Een kleinere terugloop wordt beschouwd als transmitterherstart:
alleen de geschiedenis en tellercontrole van die gauge worden gereset.
Ongeldige meetregels worden als `malformed_data` gelogd.

## Kalibratie in de GUI

Elke gauge heeft eigen instellingen via het tabblad met zijn ID:

```text
weergegeven waarde = (raw_value − zero) × gain + offset
```

- **Zero balance** neemt de laatste ruwe sample als nulreferentie en zet de
  offset terug op 0, zodat die sample exact nul weergeeft. Ontvang eerst data.
- **Offset** wordt opgeteld in de gekozen uitvoereenheid.
- **Gain** converteert counts naar de uitvoereenheid (bijvoorbeeld 0.01 kN/count).
  Negatieve gain is toegestaan; nul, NaN en oneindig niet.
- **Output unit** is het eenheidlabel, bijvoorbeeld kN of µε.
- **Apply** past offset, gain en eenheid toe. **Reset calibration** herstelt
  zero = 0, offset = 0, gain = 1 en de eenheid counts.

Voorbeeld: raw = 82731, zero = 82000, gain = 0.01 en offset = 2 geeft 9.31 kN.
Kalibratie gebeurt lokaal; er worden geen kalibratiecommando's verstuurd.
Bestaande grafiekhistorie wordt met de huidige instellingen weergegeven.
De ruwe waarde blijft naast de omgerekende waarde zichtbaar.
Instellingen blijven behouden bij Clear graphs, reconnect en transmitterreset,
maar worden niet opgeslagen bij afsluiten. Clear graphs wist de historie van
alle drie gauges; de ID-toewijzing blijft staan.

## CSV en debug

Data-CSV bevat per sample:

```text
pc_received_utc,host_elapsed_s,tx_sample_ms,tx_elapsed_s,sequence,raw_value,flags,strain_gauge_id,converted_value,unit,zero,offset,gain
```

Kalibratievelden en omgerekende waarde leggen de instellingen op het moment
van ontvangst vast. Eerdere CSV-rijen wijzigen niet na kalibreren.
PC-ontvangsttijd is UTC; TX-tijd blijft per gauge onafhankelijk.
Regels zonder `LC,` zijn firmware-/debuguitvoer en worden afzonderlijk gelogd.

## Commando's

Dashboard → receiver:

```text
start\r\n
stop\r\n
```

Deze commando's gelden voor de volledige stream; ze bevatten geen gauge-ID.
De simulator start/stopt alle drie gauges tegelijk. Receiverfirmware moet dit
gedrag voor de aangesloten transmitters ondersteunen.

## TX-simulator

[TX_simulator.py](./TX_simulator.py) stuurt `SG_1`, `SG_2` en `SG_3` met
verschillende signalen en onafhankelijke 16-bit sequencecounters via één poort.
De ingestelde frequentie is per gauge: 10 Hz betekent 30 berichten/s totaal.
Start het script, selecteer de getoonde dashboardpoort en klik op Start node.
De simulator kan ook direct starten zonder op het commando te wachten.
