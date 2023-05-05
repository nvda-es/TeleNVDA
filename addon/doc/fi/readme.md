# TeleNVDA #

* Tekijä: Asociación Comunidad Hispanohablante de NVDA ja muut
  avustajat. Alkuperäisen lisäosan tehnyt Tyler Spivey ja Christopher Toth
* Yhteensopivuus: NVDA 2019.3 ja uudemmat
* Lataa [vakaa versio][1]

Tervetuloa TeleNVDA-lisäosaan, jonka avulla voit yhdistää toiseen ilmaista
NVDA-ruudunlukuohjelmaa käyttävään tietokoneeseen. Voit yhdistää toisen
henkilön tietokoneeseen tai sallia luotetun henkilön yhdistää järjestelmääsi
ylläpitorutiinien suorittamista, ongelman diagnosointia tai koulutuksen
tarjoamista varten. Tämä on muokattu versio
[NVDA-etäkäyttö-lisäosasta](https://nvdaremote.com), ja ylläpidosta vastaa
NVDA:n espanjalaisyhteisö. Se on täysin yhteensopiva NVDA-etäkäyttö-lisäosan
kanssa. Erot ovat tällä hetkellä seuraavat:

* Asetus mahdollistaa sellaisten etäpuhekomentojen estämisen, jotka eroavat
  tekstistä.
* Paranneltu tuki välityspalvelimille ja TOR-piilopalveluille
  ([Välityspalvelintuki-lisäosa](https://addons.nvda-project.org/addons/proxy.fi.html)
  vaaditaan).
* Mahdollisuus F11-näppäinkomennon vaihtamiseen. Se toimii nyt
  yleisskriptinä, joten näppäinkomento voidaan määrittää
  Näppäinkomennot-valintaikkunassa.
* Mahdollisuus seuraavan näppäinkomennon ohittamiseen kokonaan. Tästä on
  hyötyä, mikäli sinun tarvitsee lähettää etä- ja isäntäkoneen välillä
  vaihtava näppäinkomento etäkoneelle.
* Mahdollisuus lähettää ja vastaanottaa pieniä tiedostoja (enintään 10 Mt)
  samassa istunnossa olevien käyttäjien välillä.
* Ability to forward ports via UPNP.
* Ability to use a custom portcheck service.
* Useita bugikorjauksia.

## Ennen kuin aloitat

NVDA ja TeleNVDA-lisäosa on oltava asennettuna molemmissa tietokoneissa.

Molempien asennusvaiheet ovat standardinmukaiset. Lisätietoja löytyy NVDA:n
käyttöoppaasta.

## Päivittäminen

Jos olet asentanut TeleNVDA-lisäosan suojatulle työpöydälle,  lisäosaa
päivittäessäsi on suositeltavaa, että päivität myös sen version.

Tämä tehdään päivittämällä ensin olemassa oleva lisäosa ja valitsemalla
sitten NVDA-valikosta Asetukset -> Asetukset -> Yleiset ja painamalla "Käytä
tallennettuja asetuksia kirjautumisikkunassa ja muissa suojatuissa ruuduissa
(edellyttää järjestelmänvalvojan oikeuksia)" -painiketta.

## Etäistunnon aloittaminen välittäjäpalvelimen kautta

### Hallittavassa tietokoneessa

1. Avaa NVDA-valikko ja valitse Työkalut -> Etäkäyttö -> Yhdistä tai paina
   NVDA+Alt+Page up. Tätä näppäinkomentoa on mahdollista vaihtaa NVDA:n
   Näppäinkomennot-valintaikkunasta.
2. Valitse ensimmäisestä valintapainikeryhmästä Asiakas.
3. Valitse toisesta valintapainikeryhmästä Salli tämän tietokoneen hallinta.
4. Kirjoita Isäntä-muokkauskenttään sen palvelimen osoite, johon olet
   muodostamassa yhteyttä, esim. remote.nvda.es. Jos palvelin käyttää
   vaihtoehtoista porttia, voit kirjoittaa isäntäkoneen muodossa
   &lt;isäntä&gt;:&lt;portti&gt;, esim. remote.nvda.es:1234. Jos olet
   yhdistämässä IPV6-osoitteeseen, kirjoita se hakasulkuihin,
   esim. [2603:1020:800:2::32].
5. Kirjoita Avain-muokkauskenttään haluamasi avain tai paina Luo avain
   -painiketta. Avainta käytetään tietokoneesi hallitsemiseen. Hallittavan
   koneen ja kaikkien siihen yhdistävien asiakaskoneiden on käytettävä samaa
   avainta.
6. Paina OK. Kun yhteys on muodostettu, kuulet äänimerkin ja
   "Yhdistetty"-ilmoituksen. Jos palvelin sisältää päivän viestin, se
   näytetään valintaikkunassa. Palvelimen asetuksista riippuen näet tämän
   valintaikkunan joka kerta muodostaessasi yhteyden tai vain ensimmäisellä
   kerralla.

### Hallitsevassa tietokoneessa

1. Avaa NVDA-valikko ja valitse Työkalut -> Etäkäyttö -> Yhdistä tai paina
   NVDA+Alt+Page up. Tätä näppäinkomentoa on mahdollista vaihtaa NVDA:n
   Näppäinkomennot-valintaikkunasta.
2. Valitse ensimmäisestä valintapainikeryhmästä Asiakas.
3. Valitse toisesta valintapainikeryhmästä Hallitse toista konetta.
4. Kirjoita Isäntä-muokkauskenttään sen palvelimen osoite, johon olet
   muodostamassa yhteyttä, esim. remote.nvda.es. Jos palvelin käyttää
   vaihtoehtoista porttia, voit kirjoittaa isäntäkoneen muodossa
   &lt;isäntä&gt;:&lt;portti&gt;, esim. remote.nvda.es:1234. Jos olet
   yhdistämässä IPV6-osoitteeseen, kirjoita se hakasulkuihin,
   esim. [2603:1020:800:2::32].
5. Kirjoita Avain-kenttään haluamasi avain tai paina Luo avain
   -painiketta. Hallittavan koneen ja kaikkien siihen yhdistävien
   asiakaskoneiden on käytettävä samaa avainta.
6. Paina OK. Kun yhteys on muodostettu, kuulet äänimerkin ja
   "Yhdistetty"-ilmoituksen. Jos palvelin sisältää päivän viestin, se
   näytetään valintaikkunassa. Palvelimen asetuksista riippuen näet tämän
   valintaikkunan joka kerta muodostaessasi yhteyden tai vain ensimmäisellä
   kerralla.

### Yhteyden suojausvaroitus

Jos muodostat yhteyden palvelimeen, jolla ei ole kelvollista
SSL-varmennetta, näkyviin tulee yhteyden suojausvaroitus.

Tämä voi tarkoittaa, että yhteytesi ei ole turvallinen. Jos luotat tähän
palvelimen sormenjälkeen, voit muodostaa yhteyden kerran painamalla
"Yhdistä" tai muodostaa yhteyden ja tallentaa sormenjäljen painamalla
"Yhdistä ja älä kysy uudelleen tästä palvelimesta".

## Suorat yhteydet

Yhdistä-valintaikkunan Palvelin-vaihtoehdolla voit muodostaa suoran
yhteyden.

Kun se on valittuna, valitse myös, missä tilassa yhteytesi tulee olemaan.

Toinen osapuoli yhdistää koneeseesi päinvastaista vaihtoehtoa käyttäen.

Once the mode is selected, you can use the Get External IP button to get
your external IP address and make sure the port which is entered in the port
field is forwarded correctly. If enabled on your router, you can foorward
the port using UPNP before performing portcheck.

Jos portintarkistus havaitsee, ettei porttiin (oletusarvoisesti 6837) saada
yhteyttä, näkyviin tulee siitä kertova varoitus.

Forward your port and try again. Also, ensure that the NVDA process is
allowed through Windows firewall.

Note: The process for forwarding ports, enabling UPNP or configuring Windows
firewall is outside of the scope of this document. Please consult the
information provided with your router for further instruction.

Kirjoita avain Avain-muokkauskenttään tai paina Luo avain
-painiketta. Toinen osapuoli tarvitsee yhdistämiseen ulkoisen IP-osoitteesi
lisäksi tämän avaimen. Mikäli syötit Portti-muokkauskenttään muun kuin
oletusportin (6837), varmista, että toinen osapuoli lisää isäntäkoneen
osoitteeseen vaihtoehtoisen portin muodossa &lt;ulkoinen
IP&gt;:&lt;portti&gt;.

If you want to forward the chosen port using UPNP, enable the "Use UPNP to
forward this port if possible" checkbox.

Once ok is pressed, you will be connected. When the other person connects,
you can use TeleNVDA normally.

## Etäkoneen hallinta

Kun yhteys on muodostettu, etäkoneen hallinta (esim. näppäinpainallusten tai
pistekirjoitussyötteen lähettäminen) voidaan aloittaa painamalla
hallitsevassa koneessa F11. Tätä näppäinkomentoa voidaan vaihtaa NVDA:n
Näppäinkomennot-valintaikkunasta.

Kun NVDA sanoo Hallitaan etäkonetta, painamasi näppäimistön ja pistenäytön
näppäimet suoritetaan etäkoneessa. Lisäksi, jos hallitsevassa tietokoneessa
käytetään pistenäyttöä, kaikki etäkoneen palaute näytetään siinä. Lopeta
näppäinpainallusten lähettäminen ja vaihda takaisin hallitsevaan koneeseen
painamalla uudelleen F11.

Varmista parhaan yhteensopivuuden takaamiseksi, että molemmissa koneissa on
käytössä sama näppäinasettelu.

## Istunnon jakaminen

Jotta muut voivat helposti liittyä TeleNVDA-istuntoosi, valitse
Etäkäyttö-valikosta Kopioi linkki -vaihtoehto. Voit myös tehdä tämän
nopeammin määrittämällä näppäinkomennon NVDA:n
Näppäinkomennot-valintaikkunasta.

Voit valita kahden linkkiformaatin väliltä. Ensimmäinen on yhteensopiva sekä
NVDA-etäkäytön että TeleNVDA:n kanssa ja on tällä hetkellä
suositeltavin. Toinen on yhteensopiva vain TeleNVDA:n kanssa.

Mikäli olet muodostanut yhteyden hallitsevana tietokoneena, tämä linkki
sallii jonkun muun henkilön yhdistää tietokoneeseesi sekä hänen koneensa
hallitsemisen.

Jos sen sijaan olet määrittänyt tietokoneesi hallittavaksi, linkki sallii
henkilöiden, joille sen jaat, hallita konettasi.

Useat sovellukset mahdollistavat linkin automaattisen avaamisen, mutta
mikäli se ei onnistu jossain tietyssä sovelluksessa, se voidaan kopioida
leikepöydälle ja avata Suorita-valintaikkunasta.

Note that the shared link may not work if you copy it from a server running
in direct connection mode.

## Lähetä Ctrl+Alt+Del

Ctrl+Alt+Del-näppäinyhdistelmän lähettäminen ei ole mahdollista tavalliseen
tapaan näppäinpainalluksia lähetettäessä.

Käytä tätä komentoa, mikäli sinun on lähetettävä Ctrl+Alt+Del
etäjärjestelmälle, jossa suojattu työpöytä on aktiivisena.

## Lähetä tilanvaihtonäppäin paikallisen ja etäkoneen välillä

Kun painat määritettyä näppäinkomentoa vaihtaaksesi paikallisen ja etäkoneen
välillä, sitä ei yleensä lähetetä etäkoneelle vaan se vaihtaa paikallisen ja
etäkoneen välillä.

Jos sinun on lähetettävä tämä tai mikä tahansa muu näppäinkomento
etäkoneelle, voit ohittaa tämän toiminnan seuraavaksi painetulle
näppäinkomennolle aktivoimalla Lähetä seuraava näppäinkomento -skriptin.

Tälle skriptille on määritetty oletusarvoisesti näppäinkomento
Ctrl+F11. Sitä on mahdollista vaihtaa NVDA:n
Näppäinkomennot-valintaikkunasta.

Kun tämä skripti on aktivoitu, seuraavaksi painettu näppäinkomento ohitetaan
ja lähetetään etäkoneelle "ohita seuraava näppäinkomento" -skriptin
aktivoiva näppäinkomento mukaan lukien. Kun seuraava näppäinkomento on
lähetetty, se palaa tavanomaiseen toimintaan.

## Valvomattoman tietokoneen etähallinta

Saatat joskus haluta etähallita omaa konettasi. Tämä on erityisen
hyödyllistä matkoilla ollessasi ja halutessasi hallita kotikonetta
kannettavallasi, tai kotona sisällä talossa olevaa konetta ollessasi itse
ulkona toisen koneen kanssa. Tämä on mahdollista pienen valmistelun jälkeen.

1. Avaa NVDA-valikko ja valitse Työkalut ja sitten Etäkäyttö. Paina lopuksi
   Enter Asetukset-vaihtoehdon kohdalla.
2. Valitse "Muodosta yhteys hallintapalvelimeen automaattisesti
   käynnistyksen yhteydessä" -valintaruutu.
3. Select whether to use a remote relay server or to locally host the
   connection. If you decide to host the connection, you can try to forward
   ports using UPNP by checking the provided checkbox.
4. Valitse toisesta valintapainikeryhmästä Salli tämän tietokoneen hallinta.
5. Mikäli isännöit yhteyttä itse, sinun on varmistettava, että
   hallitsevista koneista saadaan yhteys hallitsevassa koneessa
   Portti-muokkauskenttään syötettyyn porttiin (oletusarvoisesti 6837).
6. Jos haluat käyttää välittäjäpalvelinta, täytä sekä Isäntä- että
   Avain-muokkauskentät, siirry Sarkaimella OK-painikkeen kohdalle ja paina
   Enter. Luo avain -vaihtoehto ei ole käytettävissä, joten parasta on
   keksiä helposti muistettava avain, jotta voit käyttää sitä mistä tahansa
   etäsijainnista.

Voit  määrittää TeleNVDA:n muodostamaan yhteyden edistynyttä käyttöä varten
myös automaattisesti paikalliseen tai etävälittäjäpalvelimeen
hallintatilassa. Tämä tehdään valitsemalla toisena olevasta
valintapainikeryhmästä Hallitse toista konetta -vaihtoehto.

Huom: Asetukset-valintaikkunan käynnistyksen yhteydessä tapahtuvaan
automaattiseen yhteydenmuodostamiseen liittyvillä vaihtoehdoilla ei ole
vaikutusta ennen NVDA:n uudelleenkäynnistystä.

## Puheen mykistäminen etätietokoneessa

Jos et halua kuulla etäkoneen puhetta tai NVDA:n äänimerkkejä, avaa
NVDA-valikko ja valitse Työkalut -> Etäkäyttö. Siirry lopuksi
alanuolinäppäimellä kohtaan Mykistä etäkone ja paina Enter. Huom: Tämä
asetus ei poista käytöstä hallitsevan koneen pistenäytölle tuotettavaa
etäpistekirjoituspalautetta, kun hallitsevan koneen näppäinpainallusten
lähettäminen on käytössä.

## Etäistunnon lopettaminen

Lopeta etäistunto seuraavasti:

1. Lopeta etäkoneen hallinta painamalla hallitsevassa koneessa F11. NVDA:n
   pitäisi nyt sanoa tai pistenäytöllä lukea "Hallitaan paikallista
   konetta". Jos sen sijaan kuulet tai näet pistenäytöllä ilmoituksen
   etäkoneen hallitsemisesta, paina vielä kerran F11.
2. Avaa NVDA-valikko, valitse Työkalut -> Etäkäyttö ja paina Enter Katkaise
   yhteys -vaihtoehdon kohdalla.

Vaihtoehtoisesti voit katkaista istunnon yhteyden suoraan painamalla
NVDA+Alt+Page down. Tätä näppäinkomentoa voidaan vaihtaa NVDA:n
Näppäinkomennot-valintaikkunasta. Voit pitää toisen osapuolen turvassa
katkaisemalla etätietokoneen yhteyden painamalla tätä näppäinkomentoa
näppäinten lähettämisen ollessa käytössä.

## Leikepöydän lähettäminen

Etäkäyttö-valikon Lähetä leikepöytä -vaihtoehdolla voit lähettää
leikepöydällä olevan tekstin.

Kun toiminto on otettu käyttöön, kaikki leikepöydällä oleva teksti
lähetetään toisille koneille.

## Tiedostojen lähettäminen

Etäkäyttö-valikon Lähetä tiedosto -vaihtoehto mahdollistaa pienten
tiedostojen lähettämisen kaikille istunnon jäsenille hallittava kone mukaan
lukien. Huom: Voit lähettää vain tiedostoja, jotka ovat pienempiä kuin 10
Mt. Tiedostojen lähettäminen tai vastaanottaminen ei ole mahdollista
suojatuissa ruuduissa.

Huomaa myös, että tiedostojen lähettäminen saattaa käyttää liikaa kaistaa
palvelimella tiedostokoosta, samaan istuntoon yhdistäneiden tietokoneiden
määrästä ja lähetettyjen tiedostojen määrästä riippuen. Ota yhteyttä
palvelimen ylläpitäjään ja kysy, laskutetaanko verkkoliikenteestä. Harkitse
siinä tapauksessa jonkin muun alustan käyttämistä tiedostojen jakamiseen.

Kun tiedosto on vastaanotettu etäkoneilla, näkyviin tulee Tallenna nimellä
-valintaikkuna, jossa voit valita, minne tiedosto tallennetaan.

## TeleNVDA:n määrittäminen toimimaan suojatulla työpöydällä

Jotta TeleNVDA-lisäosa toimisi suojatulla työpöydällä, se on asennettava
suojatulla työpöydällä käynnissä olevaan NVDA:han.

1. Valitse NVDA-valikosta Asetukset -> Asetukset -> Yleiset.
2. Siirry Sarkaimella Käytä tallennettuja asetuksia kirjautumis- ja muissa
   suojatuissa ruuduissa (edellyttää järjestelmänvalvojan oikeuksia)
   -painikkeen kohdalle ja paina Enter.
3. Vastaa Kyllä kysymyksiin asetustesi ja lisäosien kopioinnista sekä
   mahdollisesti näkyviin tulevaan Käyttäjätilien valvonnan kehotteeseen.
4. Kun asetukset on kopioitu, paina Enter sulkeaksesi siitä kertovan
   ilmoituksen. Siirry sitten sarkaimella OK-painikkeen kohdalle ja paina
   vielä kerran Enter sulkeaksesi valintaikkunan.

Kun TeleNVDA on asennettu suojatulle työpöydälle ja jos konettasi hallitaan
etäistunnossa, puhe ja pistenäyttö ovat käytettävissä suojatulle työpöydälle
siirryttäessä.

## SSL-varmennesormenjälkien tyhjentäminen

Jos et enää luota aiemmin luottamiisi palvelinsormenjälkiin, voit tyhjentää
ne kaikki painamalla Asetukset-valintaikkunassa "Poista kaikki luotetut
sormenjäljet" -painiketta.

## Using a custom portcheck service

By default, TeleNVDA checks open ports using a service provided by the NVDA
spanish community. You can change the service URL from the options
dialog. Ensure that the port to check is part of the custom URL and the
results are returned in the expected format. A portcheck sample script is
distributed in TeleNVDA repository, so you can host your own copy if
desired.

## TeleNVDA:n muokkaaminen

This project is covered by the GNU General Public License, version 2 or
later. You may clone [this repo][2] to make alteration to TeleNVDA, provided
that you read, understand and respect the license terms. The MiniUPNP module
is licensed under a BSD-3 clause license.

### Kolmannen osapuolen riippuvuudet

Nämä voidaan asentaa pip:llä:

* Markdown
* scons

URL-käsittelijäohjelman kääntämiseen tarvitset Visual Studio 2019:n tai
uudemman.

### Lisäosan paketointi jakelua varten

1. Open a command line, change to the root of [this repo][2]
2. Suorita **scons**-komento. Jos virheitä ei ilmennyt, luotu lisäosa
   sijoitetaan nykyiseen hakemistoon.

[[!tag dev stable]]

[1]: https://www.nvaccess.org/addonStore/legacy?file=TeleNVDA

[2]: https://github.com/nvda-es/TeleNVDA
