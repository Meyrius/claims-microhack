# Zusammenfassung der Änderungen gegenüber der Ursprungsversion

Stand: 14. September 2026

Vergleichsbasis: Git-Commit `7b07340` auf `main`

Ergebnis: Alle fünf Challenges wurden mit der angepassten Umgebung erfolgreich abgeschlossen.

## 1. Überblick

Die Ursprungsversion setzte eine durch die MicroHack-/Hackbox-Plattform vorab
bereitgestellte Azure-Umgebung, zugewiesene Lab-Zugangsdaten und mehrere
plattformgebundene Hilfsskripte voraus. Die überarbeitete Version kann vollständig in
einer kundeneigenen Azure-Subscription betrieben werden.

Die wesentlichen Änderungen sind:

| Bereich | Ursprungsversion | Überarbeitete Version |
|---|---|---|
| Azure-Bereitstellung | Hackbox-/MCAPS-gebundene Bereitstellung | Kundeneigene Bereitstellung oder Anbindung vorhandener Ressourcen |
| Betriebsmodi | Nur neu bereitgestelltes Lab | `deploy` und `existing` |
| Region | Fest auf `swedencentral` ausgerichtet | Standardmäßig `westeurope`, über Konfiguration änderbar |
| Konfiguration | Verteilte Skripte und bereitgestellte Secrets | Zentrale JSON-Ressourcenbeschreibung und generierte `.env` |
| Foundry-Authentifizierung | API-Key vorausgesetzt | API-Key oder Microsoft Entra ID über `DefaultAzureCredential` |
| Storage-Authentifizierung | Shared Key/Connection String vorausgesetzt | Entra ID und Resource-ID-Verbindung; Shared Key optional für Bestandsressourcen |
| Foundry-Verbindungen | Annahmen über vorab vorhandene Verbindungen | Explizit benannte Search- und RemoteTool-Verbindungen |
| Mistral-Endpunkt | Generischer Cognitive-Services-Endpunkt | Veröffentlichter `services.ai.azure.com`-Endpunkt |
| Statement-Retrieval | Dateiname war nicht zuverlässig suchbar | Statement-ID und Quelldatei stehen im durchsuchbaren Inhalt |
| Dokumentation | Plattform- und Coach-abhängige Schritte | Durchgängiger Self-Service-Ablauf für kundeneigene Ressourcen |
| Tests | Keine Tests für die neue Automation | 22 Setup-Tests plus zwei Regressionstests |

## 2. Entwicklungsumgebung und Paketquellen

### Dev Container

In [`.devcontainer/Dockerfile`](.devcontainer/Dockerfile) wurde das Python-3.11-Image
von Debian Bullseye auf Bookworm aktualisiert. Fest eingetragene unternehmensinterne
Paketproxys wurden aus dem veröffentlichten Image entfernt. Die bestehende Bereinigung
des veralteten Yarn-APT-Repositories bleibt erhalten.

In [`.devcontainer/devcontainer.json`](.devcontainer/devcontainer.json) setzt der
Post-Create-Schritt keine persönliche Theme-Konfiguration mehr. Das neue
getrackte Skript [`.devcontainer/post-create.sh`](.devcontainer/post-create.sh)
verwendet standardmäßig die öffentlichen npm- und PyPI-Registries, installiert die
Python-Abhängigkeiten mit der festgelegten uv-Version `0.9.17` und führt `uv sync` aus.
Das uv-Binary wird dafür bereits beim Image-Build aus dem offiziellen, versionierten
Astral-GHCR-Image kopiert. Dadurch muss ein eingeschränkter PyPI-Mirror uv nicht selbst
bereitstellen.

Die bisherige Theme-Auswahl bleibt bei Bedarf als lokale Workspace-Einstellung unter
dem Git-ignorierten `.vscode/`-Verzeichnis erhalten und ist kein Bestandteil der
Kundenvorlage.

Für Unternehmensumgebungen enthält
[`.devcontainer/local.env.example`](.devcontainer/local.env.example) kommentierte
Registry-Platzhalter. Die daraus lokal erzeugte `.devcontainer/local.env` wird von Git
ignoriert. So bleiben konkrete Proxy-URLs und optionale Zugangsdaten lokal erhalten,
ohne in die Kundenvorlage zu gelangen.

Der optionale, im Lab nicht verwendete `pnpm`-Download des Node-Features ist
deaktiviert. Damit greift der Image-Build nicht mehr auf die öffentliche npm-Registry
zu, bevor `.devcontainer/local.env` verfügbar ist. Die Vorlage und Challenge 1
unterscheiden nun außerdem zwischen Docker-/Feature-Build, Paket-Registry-Mirror und
HTTP(S)-Forward-Proxy und enthalten konkrete Rebuild- und Diagnosehinweise.

Die neue Datei [`.devcontainer/devcontainer-lock.json`](.devcontainer/devcontainer-lock.json)
fixiert Versionen, Digests und Integritätswerte der Dev-Container-Features für Azure
CLI, Docker-in-Docker und Node.js.

Das ursprüngliche `uv.lock` verwies auf einen internen Paketproxy und interne
Download-Hosts. Es wird deshalb nicht mehr veröffentlicht und ist nun ebenfalls
Git-ignoriert. Lokal bleibt die vorhandene Datei nutzbar; neue Kundenumgebungen lösen
die in [`pyproject.toml`](pyproject.toml) exakt festgelegten direkten Abhängigkeiten mit
`uv sync` über ihre öffentliche oder lokal konfigurierte Registry auf.

### Zeilenenden

Die neue [`.gitattributes`](.gitattributes) erzwingt LF-Zeilenenden für Python- und
Shell-Dateien sowie Dockerfiles. Dadurch funktionieren Linux-Shebangs und Skripte auch
nach einem Checkout unter Windows zuverlässig.

## 3. Kundeneigene Azure-Ressourcen

### Zentrales Setup-Programm

Das neue Skript [`labautomation/setup_lab.py`](labautomation/setup_lab.py) ersetzt die
verteilte, Hackbox-spezifische Automation. Es bietet folgende Befehle:

| Befehl | Zweck |
|---|---|
| `check` | Lokale Konfiguration validieren |
| `check --azure` | Ressourcen, Modelle, Rollen, Verbindungen und Daten prüfen |
| `deploy` | Einen neuen kundeneigenen Azure-Stack bereitstellen |
| `deploy --what-if` | ARM-Änderungen vor der Bereitstellung anzeigen |
| `connect` | RBAC und Foundry-Verbindungen idempotent konfigurieren |
| `configure` | Container anlegen, Policen hochladen und `.env` erzeugen |
| `all` | Den zum Modus passenden vollständigen Ablauf ausführen |
| `cleanup --yes` | Ausschließlich eine nachweislich vom Skript erzeugte Resource Group löschen |

Das Setup verwendet Azure CLI, prüft Subscription und optional Tenant und arbeitet mit
provider-spezifischen ARM-API-Versionen für Foundry, Search und Storage. Fehler werden
als benutzerkorrigierbare `SetupError`-Meldungen ausgegeben.

### Konfigurationsvertrag

[`labautomation/customer-resources.example.json`](labautomation/customer-resources.example.json)
ist die neue secret-freie Vorlage. Sie beschreibt:

- Subscription, optionalen Tenant und Teilnehmer-Object-ID;
- Modus `deploy` oder `existing`;
- Resource Groups und Namen von Foundry, Foundry-Projekt, Search und Storage;
- Deploymentnamen, Formate, SKUs und Kapazitäten von `gpt-5.4` und
  `mistral-document-ai-2512`;
- Namen und API-Versionen von Search-Index, semantischer Konfiguration,
  Knowledge Sources und Knowledge Bases;
- Namen der Foundry-Search- und RemoteTool-Verbindungen;
- Container und Tags.

Die Validierung lehnt ungültige Modi, fehlende Werte, Null-UUID-Platzhalter und
unzulässige Kapazitäten ab. Im Modus `deploy` müssen alle neu erzeugten Ressourcen in
derselben Resource Group liegen. Im Modus `existing` dürfen Foundry, Search und Storage
aus unterschiedlichen Resource Groups derselben Subscription stammen.

Die lokale Datei `labautomation/customer-resources.json` und der Ownership-State
`labautomation/.setup-state.json` wurden in [`.gitignore`](.gitignore) aufgenommen.

### Bereitstellungsmodi

`deploy` erzeugt einen isolierten Stack. Vorhandene Resource Groups werden nur
verwendet, wenn dies explizit konfiguriert ist oder das Setup ihre eigene Erzeugung über
den lokalen State nachweisen kann.

`existing` überspringt die ARM-Bereitstellung und verbindet explizit benannte,
vorhandene Ressourcen. Dadurch ist die Lösung nicht mehr an Namensmuster oder eine
bestimmte Lab-Plattform gebunden.

### Sichere Bereinigung

`cleanup` ist absichtlich restriktiv:

- keine Löschung im Modus `existing`;
- zwingendes `--yes`;
- erforderlicher lokaler Ownership-State;
- Abgleich von Subscription, Deployment und Resource Group;
- Abgleich der Tags `workload`, `managed-by` und `deployment`.

Damit kann das Skript keine gemeinsam genutzte oder extern verwaltete Resource Group
versehentlich löschen.

## 4. ARM-Template

[`labautomation/azuredeploy.json`](labautomation/azuredeploy.json) wurde von einer
Hackbox-orientierten Vorlage zu einem parametrisierbaren Kundentemplate umgebaut.

### Ressourcen

Das Template erzeugt:

- ein Microsoft-Foundry-/AI-Services-Konto mit systemseitig zugewiesener Managed
  Identity;
- ein Foundry-Projekt mit eigener Managed Identity;
- Azure AI Search im Tarif `Basic` mit Managed Identity;
- ein StorageV2-Konto mit den privaten Blob-Containern `claims-data` und `policies`;
- optional die Modellbereitstellungen `gpt-5.4` und
  `mistral-document-ai-2512`;
- eine Foundry-Verbindung zu Azure AI Search;
- zwei projektbezogene RemoteTool-Verbindungen zu den MCP-Endpunkten der
  Statement- und Policen-Knowledge-Bases.

Die Region ist nun ein Parameter mit Standardwert `westeurope`. Ressourcennamen,
Modellparameter, Kapazitäten, Verbindungsnamen, Container und Tags sind ebenfalls
parametrisiert. `deployModelDeployments` und `deployRoleAssignments` erlauben eine
Trennung von Ressourcenbereitstellung und administrativer Konfiguration.

### Authentifizierungs- und Netzwerkeinstellungen

- Storage deaktiviert Shared-Key-Zugriff (`allowSharedKeyAccess: false`).
- Blob Public Access bleibt deaktiviert; der öffentliche Netzwerkendpunkt ist für den
  Zugriff aus dem lokalen Dev Container aktiviert.
- Search unterstützt Microsoft Entra ID und API-Key. Die Challenge-Skripte verwenden
  weiterhin den Search Admin Key.
- Foundry kann mit Key oder Entra ID betrieben werden. Das Setup erkennt eine durch
  Azure Policy erzwungene Deaktivierung lokaler Authentifizierung und wechselt auf
  Entra ID.

### Rollen

Das ARM-Template kann die grundlegenden Rollen für Projekt-, Foundry- und
Teilnehmeridentitäten erzeugen. `setup_lab.py connect` vervollständigt und validiert
die Rollen idempotent:

- Foundry User für die Projektidentität und optional den Teilnehmer;
- Cognitive Services User für den Teilnehmer;
- Search Service Contributor für Projekt- und Foundry-Identität;
- Search Index Data Reader für die Projektidentität;
- Storage Blob Data Reader für die Search-Identität;
- Storage Blob Data Contributor für den Teilnehmer.

Die Rolleinrichtung kann getrennt durch eine entsprechend berechtigte
Administratoridentität erfolgen, wenn die Deployment-Identität nur Ressourcen, aber
keine Role Assignments anlegen darf.

## 5. Konfiguration, Secrets und Authentifizierung

### Generierte `.env`

[`.env.example`](.env.example) wurde vom Muster für bereitgestellte Lab-Secrets zu
einem vollständigen Vertrag für kundeneigene Ressourcen erweitert. Die produktive
`.env` wird atomar durch `setup_lab.py configure` erzeugt, mit restriktiven
Dateirechten versehen und nicht im Terminal ausgegeben.

Neu beziehungsweise explizit konfigurierbar sind unter anderem:

- Search- und MCP-API-Versionen;
- semantische Konfiguration sowie Namen aller Knowledge Sources und Knowledge Bases;
- die drei eindeutigen Foundry-Verbindungsnamen;
- Storage-Accountname und Resource ID;
- der Human-Review-Schwellwert.

Secrets werden beim Erstellen von Foundry-Verbindungen über temporäre Dateien statt
als Klartext in Prozessargumenten übergeben. Fehler beim Lesen von Secrets geben keine
CLI-Ausgabe mit potenziellen Secret-Werten weiter.

### Entra-only Foundry

[`docs/claims-intake-agent.py`](docs/claims-intake-agent.py) und
[`docs/index_crash_statements.py`](docs/index_crash_statements.py) benötigen keinen
Foundry-Key mehr zwingend. Ist `MISTRAL_DOCUMENT_AI_KEY` leer, beziehen sie über
`DefaultAzureCredential` ein Token für
`https://cognitiveservices.azure.com/.default`.

Der Mistral-Endpunkt wurde auf den vom Foundry-Konto veröffentlichten Host korrigiert:

```text
https://<foundry-account>.services.ai.azure.com
```

Der OCR-Pfad bleibt:

```text
/providers/mistral/azure/ocr?api-version=2024-05-01-preview
```

Damit wurde der in Challenge 4 beobachtete `404 Not Found` des generischen
`cognitiveservices.azure.com`-Hosts behoben.

### Entra-only Storage

Bei deaktivierten Storage Shared Keys verwendet das Setup für Azure-CLI-Datenzugriffe
`--auth-mode login`. Für die Foundry-IQ-Blob-Knowledge-Source schreibt es statt eines
Shared-Key-Connection-Strings folgenden Typ von Verbindung in `.env`:

```text
ResourceId=/subscriptions/.../resourceGroups/.../providers/Microsoft.Storage/storageAccounts/.../;
```

Für vorhandene Storage-Konten mit erlaubten Shared Keys bleibt ein klassischer
Connection String als Fallback möglich.

## 6. Foundry-Verbindungen und Agent-Aktualisierung

### Eindeutige Verbindungen

Das Setup erzeugt und prüft drei benannte Verbindungen:

| Verbindung | Typ | Verwendung |
|---|---|---|
| `claims-search` | `CognitiveSearch` | Direkter Search-Index-Zugriff des Intake-Agenten |
| `claims-crash-statements-kb` | `RemoteTool` | MCP-Zugriff auf die Statement-Knowledge-Base |
| `claims-policies-kb` | `RemoteTool` | MCP-Zugriff auf die Policen-Knowledge-Base |

Bereits vorhandene Verbindungen werden nur wiederverwendet, wenn Kategorie und Ziel
übereinstimmen. Namenskollisionen mit einem anderen Ziel führen zu einem klaren Fehler,
statt eine fremde Verbindung zu überschreiben.

### Claims Intake Agent

In [`docs/claims-intake-agent.py`](docs/claims-intake-agent.py) wurde die Auswahl der
Azure-AI-Search-Verbindung stabilisiert:

- bevorzugte Auswahl über `FOUNDRY_IQ_SEARCH_CONNECTION_NAME`;
- Unterstützung beider SDK-Darstellungen des Verbindungstyps;
- verständliche Fehlermeldung mit verfügbaren Verbindungen;
- keine willkürliche Auswahl, wenn mehrere Search-Verbindungen vorhanden sind.

Der Agent wird nicht mehr allein anhand des Modellnamens als aktuell betrachtet. Die
neueste Version wird jetzt auch auf Project Connection und Search-Index geprüft. Bei
einem abweichenden Ziel wird automatisch eine neue Agent-Version mit dem korrekten Tool
angelegt.

### Claims Intelligence Agent

In [`docs/claims-intelligence-agent.py`](docs/claims-intelligence-agent.py) sind die
MCP-Endpunkte und Verbindungen nicht mehr implizit:

- MCP-API-Version kommt aus `FOUNDRY_IQ_MCP_API_VERSION`;
- Policen- und Statement-Verbindungs-IDs werden vor dem Agentenaufbau eindeutig
  aufgelöst;
- vorhandene Agent-Versionen werden anhand von Modell, Tool-Label, URL und Connection
  ID validiert;
- veraltete oder falsch verdrahtete Agent-Versionen werden durch eine korrekte Version
  ersetzt;
- die Fehlerhilfe verweist auf `setup_lab.py connect` statt auf eine erneute
  Hackbox-Bereitstellung.

## 7. Knowledge Bases und Search-Index

### Knowledge-Base-Erstellung

[`docs/create_knowledge_base.py`](docs/create_knowledge_base.py) verwendet nun die
Namen und API-Versionen aus `.env` statt fest codierter Werte.

Beim Ergänzen der semantischen Konfiguration werden vorhandene semantische
Konfigurationen nicht mehr überschrieben. Die neue Konfiguration wird zur bestehenden
Liste hinzugefügt; ein bereits gesetzter Default bleibt erhalten. Der generierte
MCP-Endpunkt verwendet die konfigurierte API-Version.

### Robuster Indexaufbau

[`docs/index_crash_statements.py`](docs/index_crash_statements.py) wurde erweitert um:

- konfigurierbare Search-API-Version;
- Mistral-Authentifizierung per Key oder Entra ID;
- Prüfung eines vorhandenen Index auf alle erforderlichen Feldtypen und Attribute;
- klare Ablehnung eines inkompatiblen bestehenden Schemas.

### Fix für exaktes Statement-Retrieval

Der Ablauf `--indexed-claim crash3` scheiterte trotz erfolgreichem HTTP-Status `200` mit:

```text
Foundry IQ did not return indexed statement 'crash3_front'.
```

Der Grund war fachlich, nicht transportseitig: `crash3_front` lag im Index, aber `id`
und `source_file` waren nur filterbare Metadaten. Die einfache Textsuche des
Intake-Agenten lieferte stattdessen andere Top-3-Dokumente.

Beim Upload wird der durchsuchbare Inhalt jetzt so präfixiert:

```text
Statement ID: crash3_front
Source File: crash3_front.jpeg

<OCR-Inhalt>
```

Nach der Aktualisierung der zehn vorhandenen Indexdokumente lieferte der reale
Agentenabruf ausschließlich `crash3_front`; Name und Policennummer wurden korrekt als
`Michael Rodriguez` und `LIAB-AUTO-001` extrahiert.

## 8. Challenge- und Walkthrough-Dokumentation

### README

[`README.md`](README.md) beschreibt nun eine kundeneigene statt extern bereitgestellte
Umgebung. Voraussetzungen, Verantwortlichkeiten, Repository-Struktur und Challenge 1
verweisen auf den neuen Setup-Ablauf. Die Abhängigkeit von zugewiesenen Lab-Konten und
einem Event-Coach wurde entfernt.

### Challenge 1

[`challenges/challenge-01.md`](challenges/challenge-01.md) wurde umfassend neu
strukturiert:

- Wahl zwischen Codespaces und lokalem Dev Container;
- Azure-CLI-Anmeldung mit der eigenen Organisationsidentität;
- Erstellen und Bearbeiten von `customer-resources.json`;
- getrennte Abläufe für `deploy` und `existing`;
- What-if, Ressourcenprüfung, RBAC, Verbindungen, Daten-Upload und `.env`;
- Validierung der vollständigen Umgebung vor Challenge 2;
- kundenseitige Berechtigungen statt vorab bereitgestellter Lab-Credentials.

### Challenge 2

[`challenges/challenge-02.md`](challenges/challenge-02.md) wurde an den tatsächlich
implementierten Ablauf angepasst:

- alle zehn Vorder- und Rückseiten werden mit `index_crash_statements.py` indexiert;
- Statement-ID und Quelldatei werden als suchbarer Inhalt dokumentiert;
- die Rolle von Search-Index, Knowledge Source und Knowledge Base wird präzisiert;
- der Intake-Agent wird korrekt als direkter Azure-AI-Search-Tool-Nutzer beschrieben;
- die explizite Search-Verbindung ersetzt die Annahme einer beliebigen Verbindung;
- Beispielabfragen verwenden einen tatsächlich vorhandenen Policy-Identifier;
- Fahrzeugschadensfotos werden klar von OCR-fähigen Statement-Bildern abgegrenzt;
- die optionale Blob-Projektverbindung ist nur für key-fähige Bestands-Storage-Konten
  vorgesehen;
- der nicht vorhandene allgemeine Portalstatus `Connected` wurde als Prüfkriterium
  entfernt; geprüft werden Ziel und Authentifizierungstyp.

### Challenge 3

[`challenges/challenge-03.md`](challenges/challenge-03.md) verweist nun auf die in
Challenge 1 konfigurierte Umgebung. Der Policen-Upload erfolgt über `setup_lab.py
configure`. Troubleshooting empfiehlt bei fehlenden Dokumenten das erneute
Konfigurieren, die Kontrolle des Containers und das Abwarten der asynchronen
Knowledge-Source-Synchronisierung.

### Challenge 4

[`challenges/challenge-04.md`](challenges/challenge-04.md) enthält neue explizite
Voraussetzungen für `.env`, Agenten und RemoteTool-Verbindungen. Zusätzlich ist der
Recovery-Schritt für ältere Indexdaten dokumentiert: `index_crash_statements.py` erneut
ausführen, damit Statement-ID und Quelldatei suchbar werden.

### Challenge 5

[`challenges/challenge-05.md`](challenges/challenge-05.md) verweist auf die in
Challenge 1 erzeugte kundeneigene Foundry-Konfiguration. Der FIDES-Code selbst wurde
nicht verändert.

### Walkthroughs

Die Lösungen unter [`walkthrough/`](walkthrough/) wurden auf den neuen Ablauf
vereinheitlicht:

- `.env` stammt aus `setup_lab.py configure`;
- die exakt festgelegten direkten Abhängigkeiten werden mit `uv sync` installiert;
- Policen-Upload und Verbindungen stammen aus dem Customer Setup;
- Troubleshooting verweist auf `connect`, `configure` und die benannten Verbindungen;
- lange Shell-Kommandos wurden für zuverlässiges Kopieren in eine Zeile gesetzt;
- nicht mehr gültige Hackbox-, Coach- und manuelle Secret-Schritte wurden entfernt.

## 9. Entfernte Legacy-Dateien

Folgende plattformgebundene Dateien wurden entfernt:

| Datei | Grund |
|---|---|
| `labautomation/deploy-lab.ps1` | Hackbox-/MicroHack-Plattformvertrag, Credential Publishing und implizite Ressourcensuche wurden durch `setup_lab.py` ersetzt |
| `labautomation/get-keys.sh` | Setzte zwingend abrufbare Foundry- und Storage-Keys voraus und wurde durch die Entra-fähige `.env`-Generierung ersetzt |
| `labautomation/lab-defaults.json` | Enthielt Hackbox-spezifische Lab-Metadaten und ist für kundeneigene Ressourcen nicht erforderlich |

Damit enthält die neue Automation keine MCAPS-spezifischen Tags, keine
`SecurityControl=Ignore`-Ausnahmen, kein `Connect-AzAccount` und keine Veröffentlichung
von Credentials an eine Lab-Plattform.

## 10. Tests und Validierung

### Neue Tests

[`tests/test_setup_lab.py`](tests/test_setup_lab.py) deckt 22 Fälle ab:

- Konfigurationsschema und Platzhaltererkennung;
- `deploy`- und `existing`-Regeln;
- Konsistenz zwischen Setup-Parametern und ARM-Template;
- Entra-only Foundry und Storage;
- korrekten veröffentlichten Mistral-Endpunkt;
- secret-freie Konsolenausgaben und sichere temporäre Dateien;
- sichere Cleanup- und Ownership-Prüfungen;
- Connection-Kollisionen und idempotente Rollen;
- Azure-Readiness-Prüfung.

[`tests/test_index_crash_statements.py`](tests/test_index_crash_statements.py) schützt
den `crash3_front`-Fix und prüft, dass Statement-ID und Quelldatei im durchsuchbaren
`content`-Feld landen.

[`tests/test_claims_intake_agent.py`](tests/test_claims_intake_agent.py) prüft, dass
Repository-Bilddateien ohne absolute Benutzer- oder Dev-Container-Pfade in Ergebnisse
geschrieben werden.

### Nachgewiesene Ergebnisse

- Vollständige Testsuite: 24 Tests erfolgreich.
- Davon `tests/test_setup_lab.py`: 22 Setup-Tests.
- Davon `tests/test_index_crash_statements.py`: 1 Retrieval-Test.
- Davon `tests/test_claims_intake_agent.py`: 1 Portabilitätstest.
- `git diff --check`: erfolgreich; nur Hinweise zur geplanten
  Zeilenenden-Normalisierung.
- JSON-Validierung für Dev Container, Feature-Lock, Kundenkonfiguration und
  ARM-Template: erfolgreich.
- Shell-Syntaxprüfung für `.devcontainer/post-create.sh`: erfolgreich.
- Der Post-Create-Ablauf wurde mit der ignorierten lokalen Registry-Konfiguration
  erfolgreich ausgeführt.
- Ruff-Prüfung aller geänderten Python- und Testdateien: erfolgreich. Die
  Executable-Bit-Prüfung wurde für den Windows-Bind-Mount ausgenommen; der Git-Index
  speichert die Testdateien korrekt als nicht ausführbar.
- Editor-Diagnosen für die geänderten Python-, Test- und Markdown-Dateien: keine
  Fehler.
- Reale Azure-Validierung: Ressourcen, Modelle, Rollen, Verbindungen, Search-Index und
  Policy-Dokumente erfolgreich verwendet.
- Mistral OCR über `services.ai.azure.com`: erfolgreich.
- Vollständiger Challenge-4-Workflow: `APPROVED`, genehmigter Betrag 14.500 USD,
  Confidence `0.96`, Consistency Score `95`, keine Human-Review-Eskalation.
- Exaktes Foundry-IQ-Retrieval für `crash3_front`: erfolgreich.
- Alle fünf Challenges wurden anschließend erfolgreich abgeschlossen.

## 11. Geänderte Dateien

### Neu

- [`.devcontainer/devcontainer-lock.json`](.devcontainer/devcontainer-lock.json)
- [`.devcontainer/local.env.example`](.devcontainer/local.env.example)
- [`.devcontainer/post-create.sh`](.devcontainer/post-create.sh)
- [`.gitattributes`](.gitattributes)
- [`labautomation/customer-resources.example.json`](labautomation/customer-resources.example.json)
- [`labautomation/setup_lab.py`](labautomation/setup_lab.py)
- [`tests/test_setup_lab.py`](tests/test_setup_lab.py)
- [`tests/test_index_crash_statements.py`](tests/test_index_crash_statements.py)
- [`tests/test_claims_intake_agent.py`](tests/test_claims_intake_agent.py)
- [`CHANGE_SUMMARY.md`](CHANGE_SUMMARY.md)

### Geändert

- [`.devcontainer/Dockerfile`](.devcontainer/Dockerfile)
- [`.devcontainer/devcontainer.json`](.devcontainer/devcontainer.json)
- [`.env.example`](.env.example)
- [`.gitignore`](.gitignore)
- [`README.md`](README.md)
- [`challenges/challenge-01.md`](challenges/challenge-01.md)
- [`challenges/challenge-02.md`](challenges/challenge-02.md)
- [`challenges/challenge-03.md`](challenges/challenge-03.md)
- [`challenges/challenge-04.md`](challenges/challenge-04.md)
- [`challenges/challenge-05.md`](challenges/challenge-05.md)
- [`docs/claims-intake-agent.py`](docs/claims-intake-agent.py)
- [`docs/claims-intelligence-agent.py`](docs/claims-intelligence-agent.py)
- [`docs/create_knowledge_base.py`](docs/create_knowledge_base.py)
- [`docs/index_crash_statements.py`](docs/index_crash_statements.py)
- [`labautomation/README.md`](labautomation/README.md)
- [`labautomation/azuredeploy.json`](labautomation/azuredeploy.json)
- [`walkthrough/challenge-02/solution-02.md`](walkthrough/challenge-02/solution-02.md)
- [`walkthrough/challenge-03/solution-03.md`](walkthrough/challenge-03/solution-03.md)
- [`walkthrough/challenge-04/solution-04.md`](walkthrough/challenge-04/solution-04.md)
- [`walkthrough/challenge-05/solution-05.md`](walkthrough/challenge-05/solution-05.md)

### Entfernt

- `labautomation/deploy-lab.ps1`
- `labautomation/get-keys.sh`
- `labautomation/lab-defaults.json`
- `uv.lock` mit internen Registry- und Download-URLs

### Erzeugtes Beispielartefakt

[`data/claims/crash1/raw/statements/crash1_front.intake.json`](data/claims/crash1/raw/statements/crash1_front.intake.json)
wurde durch einen erfolgreichen erneuten Agentenlauf aktualisiert. Geändert wurden der
Zeitstempel und die modellgenerierte Zusammenfassung. Absolute Benutzer- und
Dev-Container-Pfade wurden in beiden getrackten Intake-Beispielen durch den portablen
Repository-Pfad `data/claims/crash1/raw/statements/crash1_front.jpeg` ersetzt. Der
Intake-Agent schreibt für Repository-Assets künftig ebenfalls relative POSIX-Pfade.

## 12. Bewusst unveränderte Komponenten

Folgende Kernkomponenten benötigten für die Portierung keine Codeänderung:

- [`pyproject.toml`](pyproject.toml) und die festgelegten direkten Paketversionen;
- [`docs/claims-sequential-workflow.py`](docs/claims-sequential-workflow.py);
- [`docs/claims-security-hardening.py`](docs/claims-security-hardening.py);
- [`docs/enterprise_models.py`](docs/enterprise_models.py);
- die Claim-Bilddaten und Policendokumente.

Die Änderungen konzentrieren sich damit auf portable Bereitstellung, korrekte
Authentifizierung und Endpunkte, deterministische Verbindungsauswahl, zuverlässiges
Retrieval sowie eine mit dem tatsächlichen Ablauf übereinstimmende Dokumentation.