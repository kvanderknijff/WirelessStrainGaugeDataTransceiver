# Wireless Strain Gauge Data Transceiver

## COM Communication Protocol

Dit document beschrijft kort hoe de communicatie tussen de **receiver**
en het **dashboard** via de COM-poort verloopt.

------------------------------------------------------------------------

## 1. Seriële verbinding

  Instelling             Waarde
  ---------------------- --------------------------------------
  Baudrate               `115200`
  Communicatie           Tekstgebaseerd
  Ontvangst              Eén bericht per regel
  Dashboard → receiver   ASCII-commando's met `CRLF` (`\r\n`)

------------------------------------------------------------------------

## 2. Meetbericht

De receiver stuurt meetdata naar het dashboard in het volgende formaat:

``` text
LC,2,<sequence>,<raw_value>,<flags>,<tx_sample_ms>
```

### Voorbeeld

``` text
LC,2,1534,82731,0,1234567
```

Dit bericht bestaat uit zes velden:

  -------------------------------------------------------------------------
  Veld                                     Voorbeeld Betekenis
  --------------------- ---------------------------- ----------------------
  `LC`                                          `LC` Geeft aan dat de regel
                                                     een meetbericht is.

  Protocolversie                                 `2` Versie van het
                                                     protocol. Het
                                                     dashboard verwacht
                                                     momenteel versie `2`.

  `sequence`                                  `1534` Volgnummer van het
                                                     meetpakket. Wordt
                                                     gebruikt om te
                                                     controleren of
                                                     pakketten in de juiste
                                                     volgorde binnenkomen.

  `raw_value`                                `82731` Ruwe meetwaarde van de
                                                     strain gauge. Dit is
                                                     de waarde die het
                                                     dashboard weergeeft en
                                                     plot.

  `flags`                                        `0` Status-/flagswaarde.
                                                     Het dashboard leest
                                                     deze als integer en
                                                     kan deze hexadecimaal
                                                     weergeven.

  `tx_sample_ms`                           `1234567` Timestamp van de
                                                     transmitter in
                                                     milliseconden. Wordt
                                                     gebruikt als
                                                     tijdsbasis voor de
                                                     grafiek.
  -------------------------------------------------------------------------

------------------------------------------------------------------------

## 3. Sequence number

`sequence` wordt gebruikt om te controleren of er meetpakketten
ontbreken.

Bijvoorbeeld:

``` text
100
101
102
103
```

is een normale volgorde.

Wanneer bijvoorbeeld dit wordt ontvangen:

``` text
100
101
105
```

kan het dashboard signaleren dat de verwachte pakketten niet
opeenvolgend zijn binnengekomen.

De sequencecontrole gebruikt een **16-bit wrap**:

``` text
65534
65535
0
1
```

Na `65535` wordt dus `0` verwacht.

------------------------------------------------------------------------

## 4. Ruwe meetwaarde

`raw_value` bevat de ruwe meetwaarde van de strain gauge.

Voorbeeld:

``` text
82731
```

Het dashboard gebruikt deze waarde rechtstreeks voor de live weergave en
grafiek.

> De omzetting of kalibratie naar een fysieke eenheid is niet vastgelegd
> in de dashboardcode.

------------------------------------------------------------------------

## 5. Flags

`flags` is een integer met statusinformatie.

Voorbeeld:

``` text
0
```

Het dashboard kan deze waarde hexadecimaal weergeven.

> De betekenis van de afzonderlijke flagbits is niet vastgelegd in de
> dashboardcode.

------------------------------------------------------------------------

## 6. Transmitter timestamp

`tx_sample_ms` is de timestamp van de transmitter in milliseconden.

Voorbeeld:

``` text
1234567
```

Het dashboard gebruikt deze timestamp als tijdsbasis voor de grafiek.

De waarde wordt behandeld als een **32-bit unsigned integer**:

``` text
0 t/m 4294967295
```

Het dashboard houdt rekening met het overlopen van deze teller.

------------------------------------------------------------------------

## 7. Status- en debugberichten

Regels die **niet** beginnen met:

``` text
LC,
```

worden door het dashboard behandeld als status- of debugberichten van de
receiver/firmware.

Deze berichten worden dus niet als meetdata in de grafiek geplaatst.

------------------------------------------------------------------------

## 8. Commando's naar de receiver

Het dashboard kan via dezelfde COM-poort commando's terugsturen.

### Start

``` text
start\r\n
```

### Stop

``` text
stop\r\n
```

De commando's worden als ASCII-tekst verstuurd en afgesloten met `CRLF`.

------------------------------------------------------------------------

## 9. Communicatie in het kort

``` text
Receiver → Dashboard

LC,2,sequence,raw_value,flags,tx_sample_ms
```

``` text
Dashboard → Receiver

start
stop
```
