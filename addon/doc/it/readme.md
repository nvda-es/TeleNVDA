## Introduzione

Benvenuto nell'addon TeleNVDA, che ti consentirà di connetterti a un altro computer che esegue lo screen reader open source NVDA. Con questo componente aggiuntivo, puoi connetterti al computer di un'altra persona o consentire ad una persona fidata di connettersi al tuo per eseguire attività di manutenzione ordinaria, diagnosticare un problema o fornire formazione. Questo componente aggiuntivo è una versione modificata dell' [add-on NVDA Remote](https://nvdaremote.com) ed è gestito dalla comunità spagnola di NVDA. È completamente compatibile con il plug-in sopraccitato. Queste sono le differenze attuali:

* Un'opzione consente di fare in modo che i cambiamenti di parametri del sintetizzatore remoto (ad esempio la lingua), non cambino nel computer locale.
* Supporto migliorato per server proxy e servizi nascosti ([add-on Proxy support](https://addons.nvda-project.org/addons/proxy.en.html) necessario).
* Possibilità di modificare il tasto f11 con un altro. Ora funziona come uno script comune, quindi è possibile assegnarlo nella finestra di dialogo "Gesti e tasti di immissione".
* Possibilità di ignorare completamente il tasto successivo, utile se è necessario inviare al dispositivo remoto il tasto utilizzato per passare daldispositivo locale a quello remoto.
* Diverse correzioni di bug.

## Prima di iniziare

Dovrai aver installato NVDA su entrambi i computer e ottenere il componente aggiuntivo TeleNVDA.
Entrambe le installazioni sono standard. Se si necessita di ulteriori informazioni le si può reperire nel manuale utente di NVDA.

## Aggiornamento

Quando si aggiorna l'add-on, se è stato installato TeleNVDA anche nel desktop sicuro, si consiglia di aggiornare anche quest'ultima copia.
Per eseguire detta operazione, prima aggiornare normalmente il componente. Quindi aprire il menu di NVDA, il sottomenù preferenze, la voce impostazioni nella categoria generali e premere il pulsante con l'etichetta "Utilizza le impostazioni di configurazione salvate nella finestra di accesso a Windows e nelle schermate di sicurezza (richiede privilegi di amministratore)".
## Avvio di una sessione remota tramite un server
### Sul computer da controllare
1. Aprire il menu di NVDA, Strumenti, Accesso Remoto, Connetti.
2. Scegliere client nel primo gruppo di pulsanti radio.
3. Scegliere Consenti il controllo remoto di questo PC nel secondo gruppo di pulsanti radio.
4. Nel campo host, immettere l'host del server al quale ci si connette, per esempio remote.nvda.es. Se il server usa una porta differente dalla standard, si può inserire l'Host con la sintassi &lt;host&gt;:&lt;port&gt; (per esempio remote.nvda.es:1234). Se il server utilizza un indirizzo IPv6, immetterlo tra parentesi quadre, per esempio [2603:1020:800:2::32].
5. Inserire una  chiave d'accesso nel campo di editazione apposito, oppure premere il pulsante genera chiave d'accesso. La chiave d'accesso servirà alla persona che  deve accedere in remoto  al tuo dispositivo. Il dispositivo  controllato e tutti i suoi client devono utilizzare la stessa chiave d'accesso.
6. Premere il pulsante OK, un suono ed ed un avviso vocale vi informerà dell'avvenuta connessione. Se il server include un messaggio del giorno esso verrà riportato in una finestra di dialogo. Questa finestra sarà visibile ogni volta che ci si connette o solo la prima volta, a seconda della configurazione del server.

### Sul computer che deve controllare
1. Aprire il menu di NVDA, Strumenti, Accesso Remoto, Connetti.
2. Scegliere client nel primo gruppo di pulsanti radio.
3. Scegliere Controlla un altro PC nel secondo gruppo di pulsanti radio.
4. Nel campo host, immettere l'host del server al quale ci si connette, per esempio remote.nvda.es. Se il server usa una porta differente dalla standard, si può inserire l'Host con la sintassi &lt;host&gt;:&lt;port&gt; (per esempio remote.nvda.es:1234). Se il server utilizza un indirizzo IPv6, immetterlo tra parentesi quadre, per esempio [2603:1020:800:2::32].
5. Inserire una  chiave d'accesso nel campo di editazione apposito, oppure premere il pulsante genera chiave d'accesso. La chiave d'accesso deve essere uguale per il PC che fa da controllore e tutti quelli che devono essere controllati.
6. Premere il pulsante OK, un suono ed ed un avviso vocale vi informerà dell'avvenuta connessione. Se il server include un messaggio del giorno esso verrà riportato in una finestra di dialogo. Questa finestra sarà visibile ogni volta che ci si connette o solo la prima volta, a seconda della configurazione del server.

### Avviso di sicurezza della connessione
Se ci si connette ad un server senza un certificato SSL valido, si riceverà un avviso di sicurezza della connessione.
Ciò potrebbe significare che la connessione non è sicura. Se si ritiene attendibile questa impronta digitale del server, è possibile premere "Connetti" per connettersi una volta oppure "Connetti e non chiedere più per questo server" per connettersi e salvare l'impronta digitale.

## Connessioni dirette
L'opzione server nella finestra di dialogo connessione consente di impostare una connessione diretta.
Una volta selezionata tale opzione, seleziona la modalità della tua connessione, se controllare un Pc o essere controllato.
L'altro computer si connetterà utilizzando la modalità inversa

Una volta selezionata la modalità, è possibile utilizzare il pulsante Ottieni Ip esterno per ottenere il proprio indirizzo IP esterno ed assicurarsi che la porta inserita nel campo apposito sia correttamente aperta.

Se viene rilevato che la porta (6837 per default) non è raggiungibile, viene visualizzato un messaggio di avviso.
Aprire la porta e riprovare.

Nota: Il processo di apertura delle porte va oltre lo scopo di questa guida. Consultare le informazioni fornite con il router per ulteriori istruzioni.

Immettere una chiave d'accesso, oppure premere il pulsante genera Chiave d'accesso. L'altra persona avrà bisogno del vostro IP esterno oltre alla chiave d'accesso per connettersi. Se è stata inserita una porta differente da quella di default (6837) nel campo di editazione Porta, assicurarsi che l'altra persona aggiunga la porta alternativa per l'indirizzo host con la sintassi &lt;external ip&gt;:&lt;port&gt;.

Una volta premuto OK, sarete connessi.
Quando l'altra persona si connette, è possibile utilizzare NVDA remote normalmente.

## Controlling the remote machine

Una volta stabilita la connessione, l'utente del dispositivo di controllo può premere   F11 per iniziare il controllo del dispositivo remoto (per esempio, eseguire comandi da tastiera o imput per il braille). Questo tasto può essere modificato nella finestra gesti e tasti di immissione.
Quando NVDA annuncia  Controllo remoto, i comandi eseguiti dal dispositivo di controllo   andranno ad interagire su quello remoto. Premere nuovamente F11 per interrompere l'inoltro dei comandi e tornare alla macchina di controllo.
Per una migliore compatibilità, assicurarsi che i layout tastiera corrispondano su entrambe le macchine.

## Condividere una sessione.
Per condividere un collegamento, in modo che un'altra persona può facilmente unirsi alla vostra sessione REMOTA, selezionare  "Copia link" dal menu.
Se vi siete connessi come dispositivo di controllo, questo link permetterà di controllare il dispositivo della persona a cui inviate il link.
Se invece avete dato il consenso per controllare il vostro dispositivo da un altro, il link servirà per permettere il controllo del vostro dispositivo alla persona a cui inviate il link.
Molte applicazioni consentono di attivare questo link automaticamente, ma se non è possibile  eseguirlo dall'applicazione specifica, può essere copiato negli appunti ed eseguito da la finestra di dialogo Esegui.

## Inoltro di Ctrl + Alt + Canc
Durante l'inoltro dei tasti, non è possibile inviare la combinazione Ctrl+Alt+Canc normalmente.
Se è necessario utilizzare questo comando, e il sistema remoto si trova su desktop sicuro, utilizzare la apposita voce dal menu.

## Inoltro del tasto di commutazione tra dispositivo locale e remoto
Premendo il tasto assegnato per il passaggio dal dispositivo locale a quello remoto verrà eseguita quest'ultima funzione, il tasto non sarà quindi intercettato dal dispositivo remoto.

Se è necessario inviare questo gesto o qualsiasi altro gesto alla macchina remota, è possibile ignorare questo comportamento per il successivo gesto immediato attivando lo script ignora il gesto successivo.

Di default, questo script è assegnato alla combinazione ctrl+f11 che può essere modificata dalla finestra gesti e tasti di immissione.

Quando questo script viene invocato, il tasto successivo verrà ignorato e inviato alla macchina remota, incluso quello per attivare lo script ignora il gesto successivo. Una volta inviato un tasto, tutto tornerà al comportamento abituale.

## Controllare da  remoto un computer in modo automatico

In certi casi  può risultare indispensabile controllare il  vostro computer da remoto. Ciò è particolarmente utile se si viaggia e si desidera controllare il vostro PC di casa dal proprio laptop, o un computer in una stanza della casa, mentre si è seduti fuori, usando un altro PC. Con semplici passaggi è possibile impostare il controllo remoto in automatico.

1. Accedere al menu NVDA, scegliere Strumenti, quindi Accesso Remoto. Infine, premere Invio su Opzioni.
2. Selezionare la casella di controllo: "Connetti automaticamente al server di controllo all'avvio".
3. Selezionare se si vuole usare un server o un Host locale.
4. Selezionare il pulsante radio Consenti il controllo remoto di questo PC.
5. Se si sceglie di usare un server proprio, assicurarsi che la porta inserita sia aperta nel dispositivo controllato, e che il dispositivo che controlla ne abbia accesso. Di default la porta è 6837.
6. Se si usa un server remoto, inserire i dati nei campi Host e chiave d'accesso, premere invio sul pulsante OK. L'opzione Genera chiave non è disponibile in questa situazione. La cosa migliore è trovare una chiave che poi verrà ricordata in modo da poterla facilmente utilizzare da qualsiasi postazione remota.

Per un uso avanzato, è possibile anche configurare NVDA Remoto per connettersi a un server locale o remoto in modalità controller. Per far ciò, selezionare l'opzione Controlla un altro PC.

Nota: le opzioni relative alla connessione automatica all'avvio nella finestra di dialogo opzioni non avranno effetto fino al riavvio di NVDA.


## Silenziare la sintesi sul computer remoto
Se non si desidera ascoltare la sintesi del computer remoto, è sufficiente accedere al menu NVDA, Strumenti, Accesso Remoto. Usare la Freccia  giù fino a Silenzia dispositivo remoto, e premere Invio. Tenere presente che questa opzione non disattiva l'output braille inviato dal dispositivo di controllo al dispositivo controllato.


## Terminare una sessione remota

Per terminare una sessione remota, effettuare le seguenti operazioni:
1. Sul Pc di controllo, premere F11 per interrompere il controllo remoto. Dovreste sentire il messaggio: "Controllo locale" Se invece si sente il messaggio Controllo remoto, premere nuovamente F11.
2. Accedere al menu NVDA, poi Strumenti, Accesso Remoto e premere Invio su Disconnetti.

In alternativa è possibile premere NVDA+alt+pagina giù, combinazione che è possibile modificare dalla finestra gesti e tasti di immissione

## Inviare gli appunti
L'opzione Invia appunti nel menu remote permette di inviare del testo dagli appunti.
quando attivata, qualsiasi testo negli appunti sarà inviato agli altri Pc.

## Configurazione di NVDA remote per consentirne l'accesso  al desktop sicuro e finestra di logon

Affinché TeleNVDA funzioni sul desktop sicuro e finestra di logon, l'addon deve essere installato in una versione di NVDA che ha l'accesso al desktop sicuro e finestra di logon.
1. Aprire il menu di NVDA, il sottomenù preferenze e la voce impostazioni nella categoria generali.

2. Premere tab fino al pulsante Utilizza le impostazioni di configurazione salvate nella finestra di logon (richiede privilegi di amministratore), e premere Invio.

3. Rispondere Sì alle domande riguardanti la copia delle impostazioni e sul copiare i plugin, e rispondere affermativamente al prompt del Controllo account utente che può apparire.

4. Quando le impostazioni sono state copiate, premere Invio per confermare. Tab fino ad OK e premere invio ancora una volta per uscire dalla finestra.

Una volta che TeleNVDA viene installato sul desktop sicuro, se si viene controllati in una sessione remota, sarà possibile controllare anche la finestra di logon e Desktop Sicuro quando vi si accede.

## Cancellazione delle impronte digitali dei certificato SSL
Se si volessero cancellare tutte le impronti digitali dei certificato dei server salvati, è possibile cancellarle premendo il pulsante Elimina tutte le impronte  digitali memorizzate nella finestra opzioni del menu accesso remoto.

## Alterazione di TeleNVDA

Questo progetto è coperto dalla GNU General Public License, versione 2 o successive. È possibile clonare questo repository per apportare modifiche a TeleNVDA, a condizione di leggere, comprendere e rispettare i termini della licenza.

### Dipendenze di terze parti

Installabili tramite pip:

* Markdown
* scons

Per compilare l'eseguibile del gestore URL, è necessario Visual Studio 2019 o versioni successive.

### Per creare un pacchetto per la distribuzione del componente aggiuntivo:

1. Aprire una riga di comando passando alla radice di questo repository
2. Eseguire il comando **scons**. Il componente aggiuntivo creato, se non ci sono errori, viene posizionato nella directory corrente.
