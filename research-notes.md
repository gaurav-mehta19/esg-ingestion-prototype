# ESG Data Ingestion Platform — Research Notes
**Prepared:** 2026-05-25  
**Purpose:** Technical reference for defending data source integration decisions in live review  
**Scope:** Three real-world data sources — SAP fuel/procurement, utility electricity, corporate travel (Concur/Navan)

---

## Table of Contents

1. [SAP Fuel & Procurement Exports](#1-sap-fuel--procurement-exports)
2. [Utility Electricity Data](#2-utility-electricity-data)
3. [Corporate Travel — Concur/Navan](#3-corporate-travel--concurnavan)

---

## 1. SAP Fuel & Procurement Exports

### 1.1 Background: Why SAP MM for ESG?

SAP Materials Management (MM) is where fuel, chemicals, raw materials, and consumables are purchased and received. ESG-relevant flows (diesel, natural gas, lubricants, refrigerants, electricity purchased via procurement) appear as goods movements in SAP — typically created via transaction MIGO and stored in the material document tables MKPF (header) and MSEG (line items), with the originating purchase order data in EKKO/EKPO.

Any ESG ingestion pipeline targeting Scope 1 (fuel combustion) or Scope 3 Category 1 (purchased goods) at SAP-run organizations will encounter these tables.

**Sources:** SAP Community (community.sap.com), leanx.eu SAP table reference, beyondse16.com, sapdatasheet.org

---

### 1.2 Mechanism Comparison: IDoc vs Flat-File vs OData vs BAPI

| Mechanism | Transport | Sync/Async | Format | Typical Use Case | ESG Ingestion Suitability |
|---|---|---|---|---|---|
| **IDoc** | ALE/EDI ports, file system, RFC | Async (batch) | Fixed-width segments (1,000-byte SDATA field) | ERP-to-ERP, B2B EDI, legacy integrations | Moderate: structured, auditable, but fixed width and hard to extend |
| **Flat-file (LSMW/SE16N export)** | FTP, shared folder | Batch | CSV/TSV, no enforced schema | Ad-hoc extracts, BI loads | Low: no schema enforcement; column order varies by transaction |
| **OData (REST)** | HTTP/S | Sync (or batch via $batch) | JSON or XML | S/4HANA Cloud integrations, Fiori apps, modern iPaaS | High for S/4HANA: real-time, typed, documented schema |
| **BAPI (RFC call)** | SAP RFC | Sync | ABAP structures (typed) | Custom integrations, ABAP programs calling SAP function modules | High in-system, but requires RFC connectivity and ABAP knowledge |

**Key architectural finding:**  
SAP itself states in its S/4HANA documentation that "files are not recommended as a serious integration technology." OData APIs are the preferred modern approach for SAP S/4HANA Cloud. IDocs remain the de-facto standard for on-premise ERP-to-ERP and legacy EDI scenarios. BAPIs require direct RFC access.

> **Sources:**  
> - SAP Help Portal: [Purchase Orders Using IDocs, BAPI, or OData API](https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/9905622a5c1f49ba84e9076fc83a9c2c/e69ce957fc00be12e10000000a4450e5.html)  
> - Avotechs: [OData APIs vs EDI (IDocs): When to Use What](https://avotechs.com/blog/odata-apis-vs-idocs/)  
> - OPC Router: [SAP Interfaces Overview](https://www.opc-router.com/sap-interfaces/)

---

### 1.3 Chosen Mechanism: IDoc (MBGMCR03 / Message Type MBGMCR)

**Rationale for choosing IDoc:**  
The majority of SAP MM production landscapes in manufacturing, chemicals, and energy are still ECC 6.0 or early S/4HANA on-premise, where IDocs are the operational standard for outbound data exchange. ESG platforms integrating with these environments will most commonly receive MBGMCR-type IDocs for goods movements. The OData option is better long-term, but IDoc is the most realistic format to encounter in the field today.

**IDoc type:** `MBGMCR03`  
**Message type:** `MBGMCR`  
**Underlying BAPI:** `MB_CREATE_GOODS_MOVEMENT` (also called via `BAPI_GOODSMVT_CREATE`)  
**Direction:** Outbound from SAP (SAP pushes goods movement data to external system)

> **Sources:**  
> - SAP4TECH: [SAP Good Movement IDoc (MBGMCR03): Structure and BAPI](https://sap4tech.net/sap-good-movement-idoc/)  
> - se80.co.uk: [MBGMCR03 IDoc interface](https://www.se80.co.uk/sap-idocs/?name=mbgmcr03)  
> - nocin.eu: [ABAP IDoc MBGMCR – Goods Movement](https://nocin.eu/abap-idoc-mbgmcr-goods-movement/)  
> - SAP Community Blog: [Outbound IDOC for Post Goods Movements using message type MBGMCR](https://blogs.sap.com/2017/05/02/outbound-idoc-for-post-goods-movements-using-message-type-mbgmcr/)

---

### 1.4 IDoc Flat-File Physical Format

When an IDoc is serialized to a flat file (used for file-based transfer or archiving), the physical layout is:

- **Record 1:** Control record — follows the structure of table `EDIDC` (IDoc control record). Fixed length: 524 bytes.
- **Records 2–N:** Data records — each follows the structure of table `EDID4` (IDoc data records). Fixed length: 1,063 bytes total (63-byte envelope + 1,000-byte `SDATA` payload field).

The `SDATA` field in each data record carries the actual segment content, positionally encoded according to the segment definition (viewable in SAP transaction WE31).

```
[Record 1] EDIDC Control Record (524 bytes)
  DOCNUM   | DIRECT | IDOCTP  | MESTYP  | SNDPRT | SNDPRN | RCVPRT | RCVPRN | ...

[Record 2] EDID4 Data Record (1,063 bytes)
  DOCNUM | SEGNAM | MANDT | DOCNUM | SEGNUM | PSGNUM | HLEVEL | SDATA (1,000 chars)
  
  Where SDATA contains positional segment content, e.g. for E1BP2017_GM_HEAD_01:
  PSTNG_DATE=20231015  DOC_DATE=20231015  REF_DOC_NO=4500012345 ...

[Record 3] EDID4 Data Record — E1BP2017_GM_CODE
  SDATA: GM_CODE=02 ...

[Record 4..N] EDID4 Data Records — E1BP2017_GM_ITEM_CREATE (one per line item)
  SDATA: MATERIAL=10001234  PLANT=DE01  STGE_LOC=0001  MOVE_TYPE=101
         ENTRY_QNT=500.000  ENTRY_UOM=L   PO_NUMBER=4500012345 ...
```

> ⚠️ **UNCERTAIN:** The exact byte offsets of individual fields within `SDATA` for a given segment are defined in the ABAP Dictionary (SE11/WE31) and are version-specific. The field names above are drawn from published BAPI structure documentation and community examples, but should be validated against the specific SAP release in the target system.

> **Sources:**  
> - Heiko Evermann: [SAP IDoc: Which tables are used to store it?](https://heikoevermann.com/sap-idoc-which-tables-are-used-to-store-it/)  
> - Guru99: [SAP IDoc Tutorial](https://www.guru99.com/all-about-idocdefinition-architecture-implementation.html)  
> - SAP Community: [Inbound IDoc Format (Flat File)](https://archive.sap.com/discussions/thread/983777)  
> - TCodeSearch: [E1BP2017_GM_HEAD_01](https://www.tcodesearch.com/sap-tables/E1BP2017_GM_HEAD_01), [E1BP2017_GM_CODE](https://www.tcodesearch.com/sap-tables/E1BP2017_GM_CODE)

---

### 1.5 MBGMCR03 Segment Structure

The MBGMCR03 IDoc contains three segment types:

| Segment Name | Description | Occurs | Mandatory |
|---|---|---|---|
| `E1BP2017_GM_HEAD_01` | Material document header | 1 | Yes |
| `E1BP2017_GM_CODE` | Goods movement code (maps to transaction/movement category) | 1 | Yes |
| `E1BP2017_GM_ITEM_CREATE` | Material document line item (one per goods movement item) | 1–999,999,999 | Yes (min 1) |

---

### 1.6 Key SAP Field Names (with German originals and ESG relevance)

#### E1BP2017_GM_HEAD_01 — Header Fields

| SAP Field | German Long Name | Type | Length | Description | ESG Relevance |
|---|---|---|---|---|---|
| `PSTNG_DATE` | Buchungsdatum (Posting Date) | DATS | 8 | Date goods movement was posted; format `YYYYMMDD` | Period alignment for ESG reporting |
| `DOC_DATE` | Belegdatum (Document Date) | DATS | 8 | Date on original document (e.g. delivery note date); format `YYYYMMDD` | Differs from posting date; can cause period mismatches |
| `REF_DOC_NO` | Referenzbeleg | CHAR | 16 | Reference document number (e.g. delivery note) | Traceability |
| `GR_GI_SLIP_NO` | — | CHAR | 10 | Goods receipt/goods issue slip number | Internal reference |

#### E1BP2017_GM_CODE — Movement Code

| SAP Field | Description | Values |
|---|---|---|
| `GM_CODE` | Goods movement code | `01` = MB01 (GR for PO), `02` = MB31 (GR for prod. order), `03` = MB1A (GI), `04` = MB1B (Transfer), `05` = MB1C (Other) |

#### E1BP2017_GM_ITEM_CREATE — Line Item Fields

| SAP Field | German Long Name | Type | Length | Description | ESG Relevance |
|---|---|---|---|---|---|
| `MATERIAL` | Materialnummer | CHAR | 18 | Material number (maps to MATNR in MARA/MSEG) | Identifies the material (diesel, gas, refrigerant) |
| `PLANT` | Werk | CHAR | 4 | Plant code (maps to WERKS in T001W) | Location for Scope 1/2 attribution |
| `STGE_LOC` | Lagerort | CHAR | 4 | Storage location within plant | Sub-plant granularity |
| `MOVE_TYPE` | Bewegungsart (Movement Type, = BWART) | CHAR | 3 | Inventory movement type | Critical for filtering fuel receipts |
| `ENTRY_QNT` | Erfasste Menge | QUAN | 13 | Quantity in entry unit of measure | The quantity of fuel/material received |
| `ENTRY_UOM` | Basismengeneinheit | UNIT | 3 | Unit of measure for ENTRY_QNT (= MEINS equivalent) | Unit inconsistency risk (see 1.8) |
| `PO_NUMBER` | Bestellnummer | CHAR | 10 | Purchase order number (= EBELN in EKKO/EKPO) | Links back to PO for price/vendor |
| `PO_ITEM` | Bestellposition | NUMC | 5 | PO line item number (= EBELP in EKPO) | — |
| `BATCH` | Charge | CHAR | 10 | Batch number | Quality/traceability |
| `ORDERID` | Auftragsnummer | CHAR | 12 | Production/maintenance order number | Used for Scope 1 direct consumption |

**Note:** In the underlying MSEG table (which MBGMCR IDoc reads from/writes to), the quantity field is `MENGE` and the unit is `MEINS`. In the BAPI/IDoc interface layer, these map to `ENTRY_QNT` / `ENTRY_UOM`. The OData API `API_MATERIAL_DOCUMENT_SRV` exposes them as `QuantityInEntryUnit` / `EntryUnit`.

---

### 1.7 Underlying Database Table Structures

These are the actual SAP ERP tables from which the IDoc data originates. Direct table access (via RFC/OData) will use these native field names.

#### MKPF — Material Document Header

| Field | Description | Type | ESG Notes |
|---|---|---|---|
| `MBLNR` | Material document number | CHAR 10 | Primary key |
| `MJAHR` | Material document year | NUMC 4 | Fiscal year |
| `BUDAT` | Posting date (Buchungsdatum) | DATS 8 | YYYYMMDD — use for period assignment |
| `BLDAT` | Document date (Belegdatum) | DATS 8 | YYYYMMDD — date on physical document |
| `BKTXT` | Document header text | CHAR 25 | — |
| `USNAM` | User name | CHAR 12 | — |

#### MSEG — Material Document Item (Line Items)

| Field | Description | Type | ESG Notes |
|---|---|---|---|
| `MBLNR` | Material document number | CHAR 10 | Links to MKPF |
| `MJAHR` | Material document year | NUMC 4 | — |
| `ZEILE` | Item in material document | NUMC 4 | Line number |
| `BWART` | Movement type (Bewegungsart) | CHAR 3 | Critical filter for ESG (101 = GR from PO, 201 = GI to cost center, 261 = GI for order) |
| `MATNR` | Material number | CHAR 18 | Lookup via MARA for material description/group |
| `WERKS` | Plant (Werk) | CHAR 4 | Lookup via T001W for plant name/location |
| `LGORT` | Storage location | CHAR 4 | — |
| `MENGE` | Quantity | QUAN 13 | Actual quantity of movement |
| `MEINS` | Base unit of measure | UNIT 3 | See unit inconsistency section |
| `DMBTR` | Amount in local currency | CURR 13 | For spend-based emission estimates |
| `WAERS` | Currency key | CUKY 5 | — |
| `EBELN` | Purchase order number | CHAR 10 | Links to EKKO/EKPO |
| `EBELP` | PO item number | NUMC 5 | — |
| `LIFNR` | Vendor account number | CHAR 10 | Supplier for Scope 3 Cat. 1 |
| `BUKRS` | Company code | CHAR 4 | Legal entity — for org-level ESG boundaries |

#### EKKO — Purchasing Document Header

| Field | Description | ESG Notes |
|---|---|---|
| `EBELN` | PO number | Primary key |
| `BUKRS` | Company code | Legal entity boundary |
| `LIFNR` | Vendor account number | Supplier ID — look up in LFA1 for name/country |
| `EKORG` | Purchasing organization | — |
| `EKGRP` | Purchasing group | Buyer group |
| `AEDAT` | Creation date | — |
| `ZTERM` | Payment terms | — |

#### EKPO — Purchasing Document Item (383 fields total)

Key fields for ESG:

| Field | Description | ESG Notes |
|---|---|---|
| `EBELN` | PO number | FK to EKKO |
| `EBELP` | PO item | Line number |
| `MATNR` | Material number | — |
| `WERKS` | Plant | Location |
| `MENGE` | PO quantity | Ordered quantity |
| `MEINS` | Order unit of measure | Beware: may differ from MSEG.MEINS |
| `BPRME` | Order price unit (Bestellpreismengeneinheit) | Price per unit base |
| `NETPR` | Net price (Nettpreis) | Price in document currency |
| `PEINH` | Price unit (Preiseinheit) | Denominator for NETPR |
| `PSTYP` | Item category | — |

> **Sources:**  
> - leanx.eu: [EKPO](https://leanx.eu/en/sap/table/ekpo.html), [EKKO](https://leanx.eu/en/sap/table/ekko.html), [MSEG](https://leanx.eu/en/sap/table/mseg.html)  
> - beyondse16.com: [Complete field list of SAP table EKPO](https://beyondse16.com/2020/04/16/complete-field-list-of-sap-table-ekpo-purchasing-document-item/)  
> - sapdatasheet.org: [EKKO](https://www.sapdatasheet.org/abap/tabl/ekko.html), [MSEG-MENGE](https://www.se80.co.uk/sap-table-fields/?tabname=mseg&fieldname=menge)  
> - SAP Community: [Table Fields of PR, PO and GRN](https://community.sap.com/t5/enterprise-resource-planning-q-a/table-fields-of-pr-po-and-grn/qaq-p/4802624)

---

### 1.8 Why Plant Codes (WERKS) Need Lookup Tables

WERKS is a 4-character alphanumeric code (e.g., `DE01`, `US03`, `GB10`). The code alone is meaningless — it is an internal SAP key. To assign ESG attributes (geographic location, country, facility name, energy grid region), the ESG platform must maintain a mapping table.

**SAP's own lookup table:** `T001W` (Plants/Branches) contains:
- `WERKS` — Plant code (primary key)
- `NAME1` — Plant name
- `BWKEY` — Valuation area
- `KUNNR` — Customer number of plant
- `LIFNR` — Vendor number of plant
- Country/address fields available via linked table `T001W → ADRC`

**ESG implication:** Without the T001W lookup (or an equivalent mapping maintained in the ESG platform), plant codes are opaque. Two plants in different countries — each with different electricity grid emission factors, different regulatory reporting boundaries, and possibly different scopes — are indistinguishable from raw MSEG data.

**Additional complication:** Plant codes are company-specific and not standardized across SAP customers. `DE01` at one company is entirely different from `DE01` at another. The ESG platform must never assume cross-customer WERKS semantics.

> **Sources:**  
> - se80.co.uk: [T001W SAP Table](https://www.se80.co.uk/saptables/t/t001/t001w.htm)  
> - sap4tech.net: [SAP Plant Table](https://sap4tech.net/sap-plant-table/)  
> - SAP Community: [Master data tables for Plant](https://answers.sap.com/questions/5974953/what-are-the-master-data-tables-for-'plant'.html)

---

### 1.9 Unit of Measure (UoM) Inconsistencies

SAP stores units of measure in `MEINS` (MSEG base UoM) and `ENTRY_UOM` (IDoc/BAPI layer). SAP uses its own internal unit codes, which do not always match ISO 80000 or common ESG reporting conventions.

**Known problematic mappings for fuel ESG:**

| Commodity | Possible SAP MEINS values | ESG Reporting Standard | Risk |
|---|---|---|---|
| Diesel / petrol (liquid fuel) | `L` (litres), `LT` (litres — alternate internal code), `GAL` (US gallons), `GL` (gallons) | litres or GJ | `L` vs `LT` both mean litres but are different SAP UoM codes; must normalize |
| Natural gas | `M3` (cubic metres), `KG` (kilograms), `MMBTU` | cubic metres or GJ | Energy content varies with gas composition |
| Electricity | `KWH`, `MWH` | kWh | Scale error if MWH confused with KWH (factor of 1,000) |
| Compressed gas / refrigerants | `KG`, `T` (tonnes) | kg | Relatively consistent |

**Root cause:** SAP allows organizations to create custom units of measure. An SAP implementation that added `LTR` for litres (not a standard SAP code) will produce values unrecognizable to a generic ESG parser. The UoM code in MEINS is a lookup into table `T006` (Units of Measurement).

**The IDoc/BAPI layer adds another layer:** `ENTRY_UOM` in IDoc may be the order UoM (BPRME from EKPO), which can differ from the base unit (MEINS in MSEG). SAP internally stores a conversion factor in `MARA` / `MARM` (Material Unit of Measure), but this is not included in standard MBGMCR IDocs.

> **Sources:**  
> - SAP Blog: [Dealing with unit of measure in purchasing document and goods movement](https://blogs.sap.com/2015/06/24/dealing-with-unit-of-measure-in-purchasing-document-and-goods-movement/)  
> - SAP Community: [Units of measure conversion](https://community.sap.com/t5/enterprise-resource-planning-q-a/units-of-measure-conversion/qaq-p/7213722)  
> - sap4tech.net: [SAP Alternative Unit of Measure for Material](https://sap4tech.net/sap-alternative-unit-of-measure-material/)  
> - erpfixers.com: [Units of Measure and Their Behavior Inside Custom ABAP Code](https://www.erpfixers.com/blog/units-of-measure-and-their-behavior-inside-custom-abap-code)

---

### 1.10 Date Fields and Formats

All SAP date fields are stored internally as `YYYYMMDD` (8-character string, no separators). This applies universally:

- `BUDAT` (Buchungsdatum = Posting Date) — when the goods movement was posted to stock
- `BLDAT` (Belegdatum = Document Date) — the date on the physical document (delivery note, invoice)
- `AEDAT` (Creation date on PO header)

**Critical ESG distinction between BUDAT and BLDAT:**
- `BLDAT` = date on the vendor's delivery note. May be days or weeks before the SAP posting.
- `BUDAT` = date SAP recognizes the stock change. This is the date that drives fiscal period assignment in SAP.
- For ESG period alignment, `BUDAT` is the reliable field for period assignment. `BLDAT` is useful for matching to physical delivery evidence but should not be used alone for period bucketing.

**IDoc wire format:** In the IDoc flat file, date fields in segments appear as 8-character YYYYMMDD strings with no separators (`20231015` not `2023-10-15`). Applications reading IDoc flat files must handle this positional format.

> **Sources:**  
> - DAB Europe: [Date fields in SAP Financials](https://www.dab-europe.com/en/articles/lets-have-a-date-date-fields-in-sap-financials/)  
> - sapdatasheet.org: [MSEG-BUDAT_MKPF](https://www.sapdatasheet.org/abap/tabl/mseg-budat_mkpf.html)  
> - SAP Community: [Date Format in IDoc](https://answers.sap.com/questions/2386605/date-format.html)

---

### 1.11 Movement Type (BWART) Reference for ESG Filtering

Not all goods movements are ESG-relevant. The ESG pipeline must filter by BWART:

| BWART | Description | ESG Relevance |
|---|---|---|
| `101` | Goods receipt for purchase order (GR für Bestellung) | **High** — fuel/material received from vendor |
| `102` | Reversal of 101 | Must subtract from totals |
| `201` | Goods issue for cost center (Warenentnahme für Kostenstelle) | **High** — direct consumption (Scope 1) |
| `202` | Reversal of 201 | Must subtract |
| `261` | Goods issue for production order | **High** — raw material consumption in manufacturing |
| `262` | Reversal of 261 | Must subtract |
| `301` / `303` / `305` | Stock transfers between plants | Scope 1 location attribution only; not new consumption |
| `501` | Receipt without purchase order | May indicate fuel fills without PO — common gap |
| `551` | Scrapping | May include refrigerant disposal — ESG-relevant |

> **Sources:**  
> - SAP Community Blog: [SAP Good Movement Types](https://community.sap.com/t5/technology-blog-posts-by-members/sap-good-movement-types-list-of-sap-movement-types/ba-p/13551698)  
> - SAP Help Portal: [Goods Movement Type](https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/ee6ff9b281d8448f96b4fe6c89f2bdc8/8b7b5fe73ef54063a2bfcfbe83cebd38.html)  
> - SAP Community: [Movement types 201 & 261](https://community.sap.com/t5/enterprise-resource-planning-q-a/movment-type-201-261/qaq-p/5816073)

---

### 1.12 German Field Name Appearances in Raw Exports

SAP was originally developed in German, and German terms appear in:
1. **ABAP Dictionary field descriptions** (visible in SE11/SE16N headers in German-locale systems)
2. **IDoc segment long names** (e.g., `E1MBXYH` has "Materialbeleg-Header")
3. **Column headers in flat-file exports** if SAP system language is German
4. **Smartform/print output** used as source for some "PDF data extraction" workflows

Common German SAP terms relevant to ESG:

| German Term | English | SAP Field |
|---|---|---|
| Buchungsdatum | Posting Date | BUDAT |
| Belegdatum | Document Date | BLDAT |
| Bewegungsart | Movement Type | BWART |
| Werk | Plant | WERKS |
| Menge | Quantity | MENGE |
| Mengeneinheit | Unit of Measure | MEINS |
| Materialnummer | Material Number | MATNR |
| Buchungskreis | Company Code | BUKRS |
| Lieferant | Vendor | LIFNR |
| Nettobetrag | Net Amount | DMBTR |
| Bestellnummer | Purchase Order Number | EBELN |

---

### 1.13 OData Alternative (API_MATERIAL_DOCUMENT_SRV for S/4HANA)

For organizations on SAP S/4HANA, the preferred integration is the OData service `API_MATERIAL_DOCUMENT_SRV`.

**Entity sets:**
- `A_MaterialDocumentHeader` — maps to MKPF
- `A_MaterialDocumentItem` — maps to MSEG

**Key fields in OData (compared to IDoc/table equivalents):**

| OData Field | IDoc/BAPI Field | SAP Table Field |
|---|---|---|
| `MaterialDocument` | — | `MBLNR` |
| `MaterialDocumentYear` | — | `MJAHR` |
| `PostingDate` | `PSTNG_DATE` | `BUDAT` |
| `DocumentDate` | `DOC_DATE` | `BLDAT` |
| `GoodsMovementType` | `MOVE_TYPE` | `BWART` |
| `Material` | `MATERIAL` | `MATNR` |
| `Plant` | `PLANT` | `WERKS` |
| `QuantityInEntryUnit` | `ENTRY_QNT` | `MENGE` |
| `EntryUnit` | `ENTRY_UOM` | `MEINS` |
| `StorageLocation` | `STGE_LOC` | `LGORT` |
| `PurchaseOrder` | `PO_NUMBER` | `EBELN` |

**OData endpoint pattern:**
```
GET /sap/opu/odata/sap/API_MATERIAL_DOCUMENT_SRV/A_MaterialDocumentItem
    ?$filter=PostingDate ge datetime'2024-01-01T00:00:00'
    &$select=MaterialDocument,PostingDate,GoodsMovementType,Material,Plant,
             QuantityInEntryUnit,EntryUnit,PurchaseOrder
    &$format=json
```

> **Sources:**  
> - SAP API Business Hub: [Material Documents - Read, Create](https://api.sap.com/api/API_MATERIAL_DOCUMENT_SRV/resource)  
> - SAP Community: [How to Post Movement Types 313 and 315 Using API_MATERIAL_DOCUMENT_SRV](https://community.sap.com/t5/enterprise-resource-planning-blog-posts-by-members/how-to-post-movement-types-313-and-315-using-api-material-document-srv-in/ba-p/14396712)  
> - SAP Help Doc: [MaterialDocumentItem SDK Reference](https://help.sap.com/doc/1d369768a4a242beb804a7a4d5187ab1/1.0/en-US/com/sap/cloud/sdk/s4hana/datamodel/odata/namespaces/materialdocument/MaterialDocumentItem.html)

---

### 1.14 What Would Break in a Real Deployment

1. **Unit of measure normalization failures.** Receiving `LT` instead of `L` for litres, or a custom UoM like `LTR` created by the customer, with no entry in the ESG platform's conversion table will silently miscount fuel volumes. There is no universal SAP UoM dictionary — each customer's T006 table can contain custom entries.

2. **BUDAT vs BLDAT confusion.** Using `BLDAT` (document date) for period assignment can push fuel consumption into the wrong reporting quarter — e.g., a December 28 delivery with a January 5 posting date would be attributed to Q1 instead of Q4 under BLDAT logic.

3. **Missing reversal filtering.** Movement types 102, 202, 262 etc. are reversals. An ESG pipeline that ingests movement type 101 (GR) but ignores 102 (reversal of GR) will overstate material consumption whenever a goods receipt is cancelled or reversed. This is a common occurrence in practice.

4. **Plant code opacity.** WERKS `0001` in one SAP system is meaningless without the T001W lookup. If the plant master data is not included in the IDoc extract (it is not — T001W is a separate table), the ESG platform must maintain its own plant-to-location mapping, which requires a separate integration or manual maintenance and will drift over time as customers add/rename plants.

5. **MBGMCR03 cannot be extended.** As the community notes, MBGMCR03 is an ALE-BAPI interface and cannot be extended via standard SAP customization. Custom fields (e.g., ESG category, cost center, project code) must be passed via the `E1BPPAREX` extension segment, which requires custom ABAP development on the SAP side. Not all customers will have done this.

6. **IDoc version drift.** MBGMCR03 was the version as of releases 45A–46B. Some systems may send MBGMCR01 or MBGMCR02. The segment definitions differ by version. An ingestion pipeline must handle multiple IDoc basic type versions.

7. **Movement type 501 (GR without PO) gaps.** Fuel deliveries at remote sites, emergency purchases, or spot buys often use BWART 501 and have no EBELN/EBELP links. These orphan records cannot be enriched with PO-level data (supplier, material group, commodity classification).

8. **Multi-currency amounts.** DMBTR in MSEG is in local (document) currency. If the ESG platform uses spend for emission estimates (spend-based method), it must handle currency conversion, since MSEG.WAERS may be EUR, USD, GBP etc. even within a single extract.

---

## 2. Utility Electricity Data

### 2.1 Background: Why Utility Data for ESG?

Electricity consumption from the grid is a Scope 2 emission source under the GHG Protocol. For ESG reporting (GHG Protocol Scope 2 Guidance, CDP, TCFD), organizations need:
- Monthly (or finer) consumption in kWh
- Tariff information (to separate peak/off-peak for demand-response analysis)
- Meter-to-location mapping (to apply grid-specific emission factors)
- Billing period dates (to align with fiscal/calendar periods)

Utilities provide data through three main channels: portal CSV/XML download, PDF bills, and programmatic API.

---

### 2.2 Mechanism Comparison: Portal CSV vs PDF Bill vs Utility API

| Mechanism | Format | Granularity | Automation | Schema consistency | ESG Ingestion Suitability |
|---|---|---|---|---|---|
| **Portal CSV download** (Green Button DMD) | CSV or ESPI XML | 15-min to daily | Manual download; semi-auto via Green Button CMD | Moderate (Green Button CSV varies by utility; XML is standardized) | Good for mid-tier; requires utility-specific parsing for CSV |
| **PDF bill** | PDF | Monthly | Near-zero (requires OCR) | None — every utility has different layout | Poor — unreliable extraction, high error rate |
| **Utility API** (Green Button CMD / direct API) | JSON or ESPI XML | 15-min to daily | High — automated data pull | High for Green Button CMD; variable for proprietary APIs | Best for enterprise scale |

> **Sources:**  
> - Green Button Alliance: [Green Button Standard](https://www.greenbuttonalliance.org/green-button)  
> - Green Button Alliance: [Utility Bill Data Mapping](https://www.greenbuttonalliance.org/utility-bill-data)  
> - Bayou Energy Blog: [Utility API Must-Haves](https://blog.bayou.energy/requirements-coverage-utility-bill-data-interval-meter-data/)  
> - EnergyCAP: [Utility Bill Data and Processing](https://www.energycap.com/ebooks/utility-bill-data-and-processing/)

---

### 2.3 Chosen Mode: Portal CSV Export (Green Button Download My Data)

**Rationale:** The Green Button "Download My Data" (DMD) standard is the most widely deployed and accessible mechanism for commercial and industrial customers in the US market, supported by hundreds of utilities. It provides a structured format (either CSV or ESPI XML) downloadable without API credentials. For this research, we focus on the CSV variant because it is the format most commonly seen by ESG practitioners dealing with mid-market clients, and its inconsistencies are instructive for platform design.

---

### 2.4 Green Button Standard Overview

Green Button is based on the **Energy Services Provider Interface (ESPI)** data standard, released by NAESB (North American Energy Standards Board) in 2011. It has two components:
1. A common XML schema for energy usage information (`ESPI` schema, Atom feed format)
2. A data exchange protocol (Green Button **Connect My Data** — CMD) for automated utility-to-third-party transfer via OAuth2

The XML format uses the Atom Syndication Format to wrap custom ESPI XML entities.

> **Sources:**  
> - Green Button Alliance: [Green Button Standard](https://www.greenbuttonalliance.org/green-button)  
> - Green Button Developers: [Technology](https://dev.greenbuttonalliance.org/technology)  
> - US DOE: [Green Button](https://www.energy.gov/data/green-button)  
> - UtilityAPI: [Green Button XML Format](https://utilityapi.com/docs/greenbutton/xml)

---

### 2.5 PG&E Green Button CSV Export — Actual Column Structure

PG&E (Pacific Gas & Electric) is one of the largest US utilities and a reference implementation of Green Button. Their CSV download has the following structure:

**Metadata header (rows 1–5, non-data):**
```
Customer Name: Acme Corporation
Service Account: 1234567890
Rate Schedule: E-20 Medium General Demand-Metered Service
Start Date: 01/01/2024
End Date: 12/31/2024
```

**Data rows (row 6 onwards):**
```csv
Type,Date,Start Time,End Time,Usage,Units,Cost,Notes
Electric usage,1/1/2024,00:00,00:15,0.432,kWh,$0.08,
Electric usage,1/1/2024,00:15,00:30,0.411,kWh,$0.08,
Electric usage,1/1/2024,00:30,00:45,0.398,kWh,$0.07,
...
```

**Column definitions:**

| Column | Type | Description | ESG Notes |
|---|---|---|---|
| `Type` | String | Always "Electric usage" for electricity | Filter if gas also included |
| `Date` | String | Format: `M/D/YYYY` (not ISO 8601) | Must parse non-standard format |
| `Start Time` | String | `HH:MM` (24-hour, no timezone) | Local time — timezone must be inferred from utility/account |
| `End Time` | String | `HH:MM` | Interval end |
| `Usage` | Decimal | Consumption in interval | The key measurement |
| `Units` | String | `kWh` | ⚠️ Some utilities export `Wh` — scale by 1,000 |
| `Cost` | String | Dollar amount with `$` prefix | Non-numeric — requires stripping before parsing |
| `Notes` | String | "Estimated" if not a real meter read | Critical flag — see section 2.8 |

**Interval granularity:** PG&E's standard Green Button CSV is 15-minute intervals. Other utilities may use 30-minute or 60-minute intervals. The platform must detect and normalize interval length.

**Dual-fuel accounts:** If the account has both electricity and gas, a zip file is generated containing two separate CSV files. The gas CSV has a different `Type` value and `Units` (typically `Therms`).

> **Sources:**  
> - Ally Electric and Solar: [How to Download PG&E Green Button Data](https://www.allyelectricandsolar.com/blog/2024/11/20/how-to-download-pgampe-green-button-data-a-step-by-step-guide)  
> - wiki.dlma.com: [Analyzing PG&E data with VisiData](https://wiki.dlma.com/vd-pge) — confirms CSV column structure  
> - GitHub Gist: [PG&E Green button data downloader](https://gist.github.com/stevedh/4010535)  
> - Oracle docs (Green Button DMD): [Green Button](https://docs.oracle.com/en/industries/energy-water/digital-self-service/energy-management-overview/Content/Customer_Experience_Overview/Green_Button.htm)

---

### 2.6 Green Button ESPI XML Format — Key Structures

For utilities that provide the ESPI XML format (or for Green Button CMD API responses), the structure is an Atom feed:

```xml
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:espi="http://naesb.org/espi">

  <!-- UsagePoint entry: logical metering point -->
  <entry>
    <content>
      <espi:UsagePoint>
        <espi:ServiceCategory>
          <espi:kind>0</espi:kind>  <!-- 0=electricity, 1=gas, 2=water -->
        </espi:ServiceCategory>
      </espi:UsagePoint>
    </content>
  </entry>

  <!-- ReadingType entry: metadata about the measurement -->
  <entry>
    <content>
      <espi:ReadingType>
        <espi:accumulationBehaviour>4</espi:accumulationBehaviour>  <!-- 4=deltaData (interval) -->
        <espi:commodity>1</espi:commodity>      <!-- 1=electricity -->
        <espi:currency>840</espi:currency>      <!-- 840=USD -->
        <espi:dataQualifier>12</espi:dataQualifier>
        <espi:flowDirection>1</espi:flowDirection>  <!-- 1=forward (consumption) -->
        <espi:intervalLength>900</espi:intervalLength>  <!-- seconds; 900=15 min -->
        <espi:kind>12</espi:kind>               <!-- 12=energy -->
        <espi:powerOfTenMultiplier>0</espi:powerOfTenMultiplier>
        <espi:uom>72</espi:uom>                 <!-- 72=kWh -->
      </espi:ReadingType>
    </content>
  </entry>

  <!-- MeterReading entry containing IntervalBlocks -->
  <entry>
    <content>
      <espi:IntervalBlock>
        <espi:interval>
          <espi:duration>86400</espi:duration>    <!-- seconds (1 day) -->
          <espi:start>1704067200</espi:start>     <!-- Unix epoch UTC -->
        </espi:interval>
        <espi:IntervalReading>
          <espi:timePeriod>
            <espi:duration>900</espi:duration>    <!-- 15-minute interval -->
            <espi:start>1704067200</espi:start>   <!-- Unix epoch UTC -->
          </espi:timePeriod>
          <espi:value>432</espi:value>            <!-- Value * 10^powerOfTenMultiplier = 432 Wh = 0.432 kWh -->
          <espi:ReadingQuality>
            <espi:quality>19</espi:quality>       <!-- 19=validated, 20=estimated -->
          </espi:ReadingQuality>
        </espi:IntervalReading>
      </espi:IntervalBlock>
    </content>
  </entry>

</feed>
```

**Key parsing notes:**
- `start` timestamps are Unix epoch (seconds since 1970-01-01 UTC). Must convert to local time using the utility's timezone (not present in the XML — must be known externally).
- `value` is an integer. The actual physical value = `value × 10^powerOfTenMultiplier`. With `powerOfTenMultiplier=0` and `uom=72` (kWh), value `432` means 432 kWh. With `powerOfTenMultiplier=-3`, value `432` means 0.432 kWh. This is a frequent source of scale errors.
- `ReadingQuality.quality` code `20` = estimated. This is the machine equivalent of the CSV `Notes: Estimated` field.

> **Sources:**  
> - UtilityAPI: [Green Button XML Format](https://utilityapi.com/docs/greenbutton/xml)  
> - Green Button Alliance Dev: [The ESPI XSD](https://dev.greenbuttonalliance.org/espiusecase.html)  
> - Green Button GitHub: [OpenESPI Developers Guide](http://greenbuttonalliance.github.io/OpenESPI-GreenButton-API-Documentation/)

---

### 2.7 Tariff Structures and Their Impact

Commercial electricity tariffs are significantly more complex than residential flat rates. Common structures found in utility data:

| Tariff Component | Description | Impact on ESG Ingestion |
|---|---|---|
| **Time-of-Use (TOU) rates** | Peak / off-peak / shoulder pricing by time of day | CSV exports combine all intervals in one column; tariff register separation requires separate meter reads or TOU-aware parsing |
| **Demand charges** | Based on maximum kW in a 15- or 30-minute interval within the month | Not present in interval CSV (kWh only); demand peak requires separate computation from interval data |
| **Tiered / block rates** | Price per kWh changes once consumption crosses a threshold | Total cost calculation requires cumulative monthly totals |
| **Power factor charges** | Additional charge for poor power factor (reactive power) | Not in standard Green Button interval data |
| **Transmission / distribution charges** | Fixed charges per kWh or per month | Present in bill-level data (UsageSummary) but not in interval data |
| **Net metering / export credits** | Solar export offsets consumption | Green Button `flowDirection` field distinguishes import (1) from export (19); ESG must handle both directions |

**Green Button bill-level data (UsageSummary XML):**
The `UsageSummary` entity in ESPI provides billing-period aggregate data:
- `billingPeriod.duration` — length of billing period in seconds
- `billingPeriod.start` — Unix epoch start of billing period
- `billLastPeriod` — monetary amount for last billing period (in hundred-thousandths of currency unit)
- `billToDate` — running total for current period
- `costAdditionalLastPeriod` — additional charges (taxes, admin fees, demand charges)
- Demand charge fields: ⚠️ **UNCERTAIN** — Green Button Alliance's Utility Bill Data Mapping specification includes demand charges in the UsageSummary, but the exact element names and their consistency across utility implementations is not fully documented in publicly accessible sources.

> **Sources:**  
> - Green Button Alliance: [Utility Bill Data Mapping](https://www.greenbuttonalliance.org/utility-bill-data)  
> - Energy Tool Base: [Decoding Electricity Rates: Creating an Energy Use Profile](https://www.energytoolbase.com/blog/utility-rates/decoding-electricity-rates-creating-an-energy-use-profile-part-3/)

---

### 2.8 Billing Period Misalignment with Calendar Months

This is the single most consequential data quality problem for ESG platforms ingesting utility data.

**The problem:** Utilities read meters on a cycle that does not align with calendar month boundaries. A typical scenario:

```
Bill 1:  Read 2023-11-22 → 2023-12-19  (27 days — includes 19 days of December)
Bill 2:  Read 2023-12-19 → 2024-01-23  (35 days — spans December and January)
Bill 3:  Read 2024-01-23 → 2024-02-20  (28 days)
```

**Downstream consequences for ESG:**

1. **Calendar year allocation.** If an organization reports Scope 2 emissions on a January–December calendar year, neither Bill 1 nor Bill 2 directly maps to December. The platform must prorate by day, which requires knowing daily consumption (available from interval data) or making a uniform daily assumption.

2. **Estimated vs. actual reads.** When a meter reader cannot access the meter (locked gate, bad weather), the utility generates an **estimated bill** based on historical usage. The CSV `Notes` field will say "Estimated". The next actual read will correct the running total, which can cause a sudden spike (catch-up bill) or dip. ESG platforms must either:
   - Flag estimated intervals and exclude from final reports, or
   - Apply an accrual/reversal adjustment when the actual read arrives.
   
   Accrual accounting practice: estimate the utility cost based on historical data, then reverse the accrual when the actual bill arrives. This creates timing differences in ESG emissions data.

3. **Leap billing periods.** Some utilities issue bills for 28 days, some for 35. A simple "divide by 30 for monthly average" is incorrect. Annual totals are accurate, but quarterly or monthly breakdowns from bill-level data (not interval data) will be systematically wrong.

4. **Time zone ambiguity.** Green Button timestamps are epoch UTC. The CSV uses local time without timezone annotation. Utilities in states spanning two time zones (e.g., Indiana, Arizona with DST exceptions) can produce ambiguous timestamps at DST transitions.

5. **Multi-site accounts.** Large commercial customers may have a single account covering multiple meters at multiple locations. The Green Button export may aggregate all meters, making it impossible to attribute consumption to individual facilities without the meter-to-location mapping table maintained by the customer.

> **Sources:**  
> - Solstice: [Estimated vs. Actual Readings — How They Affect Your NYSEG Bill](https://solstice.us/solstice-blog/nyseg-bill-estimated-versus-actual-readings/)  
> - ABC Energy: [Understanding Estimated Meter Reads and Actual Meter Reads](https://abcenergy.com/estimated-meter-reads-bill-adjustments)  
> - IBM Envizi: [Utility Bill Analytics Reports](https://www.ibm.com/docs/en/envizi-esg-suite?topic=analytics-utility-bill-reports)  
> - GHG Protocol Scope 2 Guidance: [Exec Summary](https://ghgprotocol.org/sites/default/files/2022-12/Scope2_ExecSum_Final.pdf)

---

### 2.9 UK Smart Meter Data (SMETS2) — Brief Note

For UK deployments, the relevant standard is SMETS2 (Smart Metering Equipment Technical Specification v2). Key characteristics:
- ESME (Electricity Smart Metering Equipment) captures half-hourly consumption
- Data is transmitted via DCC (Data Communications Company) network using DLMS/COSEM over ZigBee SEP (HAN) and GPRS/LTE (WAN)
- Consumer-accessible formats: UK utilities provide half-hourly data via online portals, but download formats vary widely (some CSV, some proprietary). There is no UK equivalent of the US Green Button standard that is universally deployed.
- The SERL (Smart Energy Research Lab) provides half-hourly anonymized research datasets in CSV format, but this is academic/research data, not the operational utility export format.

> ⚠️ **UNCERTAIN:** The exact CSV column structure for UK smart meter downloads varies significantly by utility (British Gas, EDF, E.ON, Octopus Energy all have different portal formats). No single UK national standard equivalent to Green Button DMD was confirmed in publicly accessible documentation.

> **Sources:**  
> - Jack Kelly: [SMETS2 Technical Specification](https://jack-kelly.com/smart_meters_equipment_tech_spec_version_2_smets2)  
> - UK Data Service: [SERL Smart Meter Documentation](https://doc.ukdataservice.ac.uk/doc/8666/mrdoc/pdf/8666_serl_smart_meter_documentation_edition07.pdf)

---

### 2.10 What Would Break in a Real Deployment

1. **Estimated read contamination.** Ignoring the `Notes: Estimated` flag in CSV (or `ReadingQuality.quality=20` in XML) means estimated consumption values get treated as actuals. A cold snap that causes a utility to estimate high will inflate Scope 2 emissions for that period. The subsequent correction bill will show anomalously low consumption. Both distort the time series.

2. **`powerOfTenMultiplier` mis-parsing.** In ESPI XML, the multiplier field controls the scale of `value`. If the parser hardcodes `value / 1000` assuming Wh→kWh conversion, but the file uses `powerOfTenMultiplier=0` with `uom=72` (kWh), it will report consumption 1,000 times smaller than actual. This is a silent failure.

3. **Billing period to calendar period proration.** A naive implementation that assigns the entire bill to the month in which the bill was issued will be wrong for every bill. For a December 19 → January 23 bill issued in January, the naive approach attributes 35 days of consumption to January and zero to December.

4. **Missing meter-to-site mapping.** Green Button data identifies a `UsagePoint` by an ID internal to the utility. Without a table mapping utility meter IDs to physical addresses, buildings, or cost centers, the ESG platform cannot assign emissions to organizational units for boundary-scoped reporting.

5. **CSV column header variations across utilities.** Green Button CSV has no enforced schema for column names or order — the standard recommends content but does not mandate exact column headers. A parser written for PG&E CSV (columns: Type, Date, Start Time, End Time, Usage, Units, Cost, Notes) may fail on SCE or ConEd exports which use different column names or orderings. The ESPI XML format is more consistent but less widely deployed.

6. **Time zone handling at DST transitions.** The Green Button epoch timestamps are UTC, but the `intervalLength` is a fixed number of seconds. At the spring-forward transition, there is a 23-hour day, and at fall-back, a 25-hour day. Interval aggregations to daily or monthly totals must account for DST correctly in local time.

7. **kWh vs kW confusion in demand charge data.** Utilities report both energy (kWh) and peak demand (kW) in some export files. Mixing these up in ESG calculations (e.g., treating a peak demand reading as energy consumption) is a common data quality error.

---

## 3. Corporate Travel — Concur/Navan

### 3.1 Background: Why Corporate Travel for ESG?

Business travel emissions fall under **GHG Protocol Scope 3, Category 6** (Business Travel). Activities covered include:
- Commercial air travel (domestic and international)
- Hotel stays
- Rail travel
- Rental cars
- Taxis, rideshare, ground transport
- Charter flights

Under the GHG Protocol Technical Guidance for Scope 3 Category 6, organizations must use either:
1. **Distance-based method:** Activity data (passenger-km by mode) × emission factor (kg CO2e per passenger-km)
2. **Spend-based method:** Expenditure × spend-based emission factor (kg CO2e per $)

Corporate travel management systems (Concur, Navan, Egencia, Amex GBT) are the primary data source for employee travel records.

> **Sources:**  
> - GHG Protocol: [Technical Guidance for Calculating Scope 3 Emissions — Category 6](https://ghgprotocol.org/sites/default/files/standards_supporting/Chapter6.pdf)  
> - PlanA.Earth: [Scope 3 Category 6](https://plana.earth/glossary/scope-3-category-6)  
> - SQUAKE: [How to Calculate Scope 3.6 Emissions](https://www.squake.earth/blog/how-to-calculate-scope-3-6-emissions-from-business-travel)

---

### 3.2 SAP Concur API Overview

SAP Concur exposes travel and expense data through multiple versioned REST APIs:

| API | Version | Status | Format | Primary Use |
|---|---|---|---|---|
| Expense Reports | v3 | Active (legacy) | JSON | Report-level and entry-level expense data |
| Expense Reports | v4 | Current | JSON | Report headers; entries via separate endpoint |
| Expense Entries | v3 | Active | JSON | Individual expense line items |
| Quick Expense | v4 | Current | JSON | Lightweight expense creation/read |
| Itinerary/Booking | v1 (Booking resource) | Active | XML and JSON | Travel booking segments (air, hotel, car) |
| Itinerary | v4 | Preview/emerging | JSON | Trip-level data |
| Travel Request | v4 | Current | JSON | Pre-trip approval data |

Authentication uses OAuth 2.0 (company JWT tokens or user-level tokens).

> **Sources:**  
> - SAP Concur Developer Center: [Reports v3](https://developer.concur.com/api-reference/expense/expense-report/v3.reports.html)  
> - SAP Concur Developer Center: [Reports v4](https://developer.concur.com/api-reference/expense/expense-report/v4.reports.html)  
> - SAP Concur Developer Center: [Expense Entries v3](https://developer.concur.com/api-reference/expense/expense-report/expense-entry.html)  
> - SAP API Hub: [Concur Expense REST API](https://api.sap.com/package/ConcurExpense)

---

### 3.3 Concur Expense Report — Report-Level Fields (v3 API)

`GET /api/v3.0/expense/reports`

```json
{
  "ID": "39BD0F4D77E148...",
  "Name": "Q4 Business Travel — London",
  "Total": 2847.50,
  "CurrencyCode": "USD",
  "Country": "US",
  "CountrySubdivision": "US-CA",
  "CreateDate": "2024-11-15T00:00:00",
  "SubmitDate": "2024-11-18T00:00:00",
  "ProcessingPaymentDate": "2024-11-25T00:00:00",
  "PaidDate": "2024-11-28T00:00:00",
  "OwnerLoginID": "jsmith@acme.com",
  "OwnerName": "John Smith",
  "ApproverLoginID": "mwilson@acme.com",
  "ApproverName": "Mary Wilson",
  "ApprovalStatusCode": "A_APPR",
  "ApprovalStatusName": "Approved",
  "PaymentStatusCode": "P_PAID",
  "PaymentStatusName": "Paid",
  "LastModifiedDate": "2024-11-28T00:00:00",
  "PersonalAmount": 0,
  "AmountDueEmployee": 2847.50,
  "AmountDueCompanyCard": 0,
  "TotalClaimedAmount": 2847.50,
  "TotalApprovedAmount": 2847.50,
  "LedgerName": "DEFAULT",
  "PolicyID": "POL001",
  "ReceiptsReceived": true
}
```

> **Sources:**  
> - SAP Concur Developer Center: [Reports v3](https://developer.concur.com/api-reference/expense/expense-report/v3.reports.html)  
> - SAP Concur Community: [Difficulties using API Expense/Reports V4](https://community.concur.com/t5/Concur-Expense-Forum/Difficulties-using-API-Expense-Reports-V4/td-p/67719)

---

### 3.4 Concur Expense Entry — Line Item Fields (v3 API)

`GET /api/v3.0/expense/entries?reportID={ID}`

This is the most ESG-relevant endpoint — it provides individual expense line items.

```json
{
  "ID": "84FCDE34B12A...",
  "ReportID": "39BD0F4D77E148...",
  "ExpenseTypeCode": "AIRFR",
  "ExpenseTypeName": "Airfare",
  "SpendCategoryCode": "TRAVEL",
  "SpendCategoryName": "Travel",
  "TransactionDate": "2024-11-10T00:00:00",
  "TransactionAmount": 1245.00,
  "TransactionCurrencyCode": "USD",
  "PostedAmount": 1245.00,
  "ApprovedAmount": 1245.00,
  "VendorDescription": "BRITISH AIRWAYS",
  "LocationName": "London, United Kingdom",
  "LocationCountry": "UK",
  "LocationSubdivision": "",
  "IsPersonal": false,
  "IsBillable": false,
  "HasImage": true,
  "HasAttendees": false,
  "FormID": "gWr7TaEt...",
  "JourneyDistance": null,
  "UnitOfMeasure": null,
  "Comment": "NYC-LHR round trip",
  "Custom1": null,
  "Custom2": null,
  "Custom3": null,
  "URI": "https://www.concursolutions.com/api/v3.0/expense/entries/84FCDE34B12A"
}
```

**Key observation for ESG:** `JourneyDistance` is `null` for airfare entries. There is no origin airport code or destination airport code in the expense entry payload. The transaction amount and vendor name are available; the geographic route is not.

---

### 3.5 Concur Expense Type Codes for Travel

The `ExpenseTypeCode` field maps to configurable expense types. Common codes used for travel-relevant ESG categories:

| ExpenseTypeCode | Common Mapping | GHG Protocol Category 6 Activity |
|---|---|---|
| `AIRFR` | Airfare | Commercial air travel |
| `LODNG` | Hotel / Lodging | Hotel stays |
| `CARRT` | Car Rental | Rental car (road) |
| `TAXCB` | Taxi / Rideshare | Ground transport |
| `MILEG` | Personal car mileage | Personal vehicle business use |
| `RAILF` | Rail / Train | Rail travel |
| `PARKG` | Parking | (Generally not emission-attributed) |

> ⚠️ **UNCERTAIN:** The codes `AIRFR`, `LODNG`, `CARRT` are commonly referenced in SAP Concur community documentation and customer guides (e.g., Ohio University Concur guide, Texas State University guide). However, expense type codes are **configurable per Concur tenant** — an organization can rename or recode expense types. The codes above are common defaults, not universal constants. An ESG platform must not hardcode these values; it must retrieve valid codes via the Expense Group Configurations endpoint.

> **Sources:**  
> - SAP Concur Developer Center: [Expense Group Configurations](https://developer.concur.com/api-reference/expense/expense-report/expense-group-configurations.html)  
> - SAP Help Portal: [Spend Categories](https://help.sap.com/docs/CONCUR_EXPENSE/1f13d54352684d6dba6e65c8c5d75ead/08bd7f8c7bfa4f15b66ed00248190480.html)  
> - OU Expense Types Guide: [Concur Expense Type Description Guide](https://ou.edu/content/dam/financialservices/Concur%20Travel/FSS%20-OU%20Expense%20Types.pdf)

---

### 3.6 Concur Itinerary API — Flight Booking Data

The Itinerary/Booking API provides structured travel booking data that is **richer than expense entry data** for ESG purposes. This is the correct API to use for distance-based emission calculations.

`GET /api/travel/trip/v1.1` or Itinerary v4 endpoint

The Booking resource contains `Segments`, which includes an `Air` element per flight segment:

```xml
<!-- Itinerary API v1 — Air Segment (XML format) -->
<Booking>
  <RecordLocator>ABC123</RecordLocator>
  <BookingSource>BRITISH AIRWAYS</BookingSource>
  <DateModifiedUtc>2024-10-15T14:32:00</DateModifiedUtc>
  <Segments>
    <Air>
      <Vendor>BA</Vendor>
      <VendorName>BRITISH AIRWAYS</VendorName>
      <Status>HK</Status>
      <StartDateLocal>2024-11-10T08:30:00</StartDateLocal>
      <EndDateLocal>2024-11-10T20:45:00</EndDateLocal>
      <StartDateUtc>2024-11-10T13:30:00</StartDateUtc>
      <EndDateUtc>2024-11-11T01:45:00</EndDateUtc>
      <StartCityCode>JFK</StartCityCode>
      <EndCityCode>LHR</EndCityCode>
      <FlightNumber>178</FlightNumber>
      <ClassOfService>Y</ClassOfService>
      <AircraftCode>789</AircraftCode>
      <Duration>420</Duration>
      <ConfirmationNumber>BA178CONF</ConfirmationNumber>
      <DateCreatedUtc>2024-10-01T09:00:00</DateCreatedUtc>
    </Air>
  </Segments>
</Booking>
```

> ⚠️ **UNCERTAIN:** The exact field names in the XML response (`StartCityCode`, `EndCityCode`, `ClassOfService`, `AircraftCode`) are documented in the SAP Concur Booking Resource documentation (developer.concur.com/api-reference/travel/itinerary/booking/booking-resource.html) and referenced in the public GitHub repository (SAP-docs/preview.developer.concur.com). However, I was unable to retrieve a complete verified sample response from primary source during this research. The field names above are consistent with what multiple community references describe, but should be verified against the live API documentation before implementation.

> **Sources:**  
> - SAP Concur Developer Center: [Booking Resource](https://developer.concur.com/api-reference/travel/itinerary/booking/booking-resource.html)  
> - SAP Concur Developer Center: [Itinerary Web Service (TMC/Third-Party)](https://developer.concur.com/api-reference/travel/itinerary-tmc-thirdparty/)  
> - GitHub (SAP-docs): [v4.itinerary.md](https://github.com/SAP-docs/preview.developer.concur.com/blob/main/src/api-reference/travel/itinerary-v4/v4.itinerary.md)

---

### 3.7 The Distance Problem: Airport Codes, Not Distances

This is the central ESG challenge with Concur travel data.

**What you get:**
- `StartCityCode: JFK` (IATA airport code)
- `EndCityCode: LHR` (IATA airport code)
- `ClassOfService: Y` (IATA class code: Y=economy, C=business, F=first)

**What you do NOT get:**
- Flight distance in km or miles
- Great-circle distance
- Actual flight path distance (which is longer than great-circle due to routing)
- Radiative forcing index (RFI) for high-altitude emissions

**What you must compute or look up:**
1. Convert airport codes to lat/lon coordinates (via airport database, e.g., OurAirports dataset)
2. Compute great-circle distance: `d = 2R × arcsin(√(sin²(Δlat/2) + cos(lat1)cos(lat2)sin²(Δlon/2)))`
3. Apply distance uplift factor (typically 1.08–1.09 per DEFRA, to account for non-direct routing)
4. Classify as short-haul (<3,700 km) or long-haul (>3,700 km) per DEFRA definitions
5. Apply class-of-service multiplier (IATA class code Y/W/C/F → economy/premium economy/business/first)

**From the Expense Entry (not Itinerary API):**
If using only the expense entry (no itinerary access), there is even less information:
- `VendorDescription: BRITISH AIRWAYS` — carrier name only
- `TransactionAmount: 1245.00` — ticket cost
- `LocationName: London, United Kingdom` — destination city (not airport)
- `JourneyDistance: null` — absent
- No origin city in expense entry (only destination)

**The Concur community explicitly confirms this problem:** When companies try to add a custom field for "from location, to location and mileage" in Concur, it appears on every expense type (not just air) and is free text, so employees enter inaccurate values. The recommended solution is integration with a third-party carbon tool (e.g., Thrust Carbon) that computes distances from the itinerary booking data.

> **Sources:**  
> - SAP Concur Community: [Other transportation — mileage capture for CO2 reporting](https://community.concur.com/t5/Concur-Expense-Forum/Other-transportation-mileage-capture-for-CO2-reporting/m-p/81873)  
> - Thrust Carbon: [Thrust Carbon and SAP Concur](https://thrustcarbon.com/thrust-carbon-business-intelligence-tools-for-sap-concur-customers)  
> - Greenplaces: [Scope 3 business travel: carbon accounting methods that hold up](https://greenplaces.com/articles/scope-3-business-travel-carbon-accounting-methods-that-hold-up/)

---

### 3.8 Hotel Expense Fields

Hotel expense entries (`ExpenseTypeCode: LODNG`) in the Concur Expense API:

```json
{
  "ExpenseTypeCode": "LODNG",
  "ExpenseTypeName": "Hotel",
  "TransactionDate": "2024-11-10T00:00:00",
  "TransactionAmount": 289.00,
  "TransactionCurrencyCode": "USD",
  "VendorDescription": "HILTON LONDON BANKSIDE",
  "LocationName": "London, United Kingdom",
  "LocationCountry": "UK",
  "IsPersonal": false,
  "Comment": "2 nights"
}
```

**Missing ESG fields:**
- Number of room nights (must be inferred from check-in/check-out dates or `Comment` field — unreliable)
- Hotel star rating or brand (determines emission factor lookup)
- Geographic coordinates of hotel (for grid-specific emission factor)

**From Itinerary API (Hotel segment):**

```xml
<Hotel>
  <Vendor>HI</Vendor>
  <VendorName>HILTON LONDON BANKSIDE</VendorName>
  <Status>GK</Status>
  <StartDateLocal>2024-11-10T15:00:00</StartDateLocal>
  <EndDateLocal>2024-11-12T11:00:00</EndDateLocal>
  <StartCityCode>LON</StartCityCode>
  <ConfirmationNumber>HILTON-UK-98765</ConfirmationNumber>
  <RoomType>STANDARD KING</RoomType>
</Hotel>
```

**For emission calculation:**
- Room nights = EndDate − StartDate (2 nights in this example)
- Location → country-level hotel emission factor (kg CO2e per room night) per DEFRA or GHG Protocol
- No per-hotel emission data available from Concur — hotel chain-specific data would require integration with hotel sustainability programs (e.g., HCMI hotel footprint data, Cornell Hotel Sustainability Benchmarking)

> ⚠️ **UNCERTAIN:** The specific field names in the Hotel segment XML (`RoomType`, `StartCityCode` vs `StartLocation`) may vary between Itinerary API versions (v1 vs v4). The v4 Itinerary API is documented in preview state as of this research.

---

### 3.9 Ground Transport / Car Rental Fields

Car rental entries (`ExpenseTypeCode: CARRT`) in Expense API:

```json
{
  "ExpenseTypeCode": "CARRT",
  "ExpenseTypeName": "Car Rental",
  "TransactionDate": "2024-11-12T00:00:00",
  "TransactionAmount": 145.00,
  "VendorDescription": "ENTERPRISE RENT-A-CAR",
  "LocationName": "London Heathrow Airport",
  "JourneyDistance": null,
  "UnitOfMeasure": null
}
```

**From Itinerary API (Car segment):**

```xml
<Car>
  <Vendor>ET</Vendor>
  <VendorName>ENTERPRISE</VendorName>
  <Status>GK</Status>
  <StartDateLocal>2024-11-10T10:00:00</StartDateLocal>
  <EndDateLocal>2024-11-12T10:00:00</EndDateLocal>
  <StartCityCode>LHR</StartCityCode>
  <EndCityCode>LHR</EndCityCode>
  <CarType>ICAR</CarType>
  <AirCondition>R</AirCondition>
  <PhysicalDescription>INTERMEDIATE</PhysicalDescription>
</Car>
```

**ESG challenges:**
- `CarType` uses ACRISS car classification codes (e.g., `ICAR` = Intermediate Car, Automatic, Air conditioning, Regular fuel). ACRISS codes encode fuel type but not engine size or exact emission rating.
- No mileage driven — only rental duration and cost. Must use spend-based emission factors.
- `JourneyDistance` is `null` in expense entry — same problem as airfare.

**Mileage expenses (`ExpenseTypeCode: MILEG`):**
For personal car mileage reimbursements, Concur does capture distance:
```json
{
  "ExpenseTypeCode": "MILEG",
  "JourneyDistance": 145.0,
  "UnitOfMeasure": "mile"
}
```
This is the one travel expense type where distance is typically available. However, UoM can be `mile` or `km` depending on tenant configuration — must normalize.

> **Sources:**  
> - SAP Concur Developer Center: [Expense Entries v3](https://developer.concur.com/api-reference/expense/expense-report/expense-entry.html)  
> - SAP Concur Developer Center: [Booking Resource](https://developer.concur.com/api-reference/travel/itinerary/booking/booking-resource.html)

---

### 3.10 Navan (Formerly TripActions) — API Overview

Navan (rebranded from TripActions in 2023) is a direct competitor to Concur with a younger REST API. Key characteristics:

- **Public API availability:** Requires explicit provisioning by Navan for each customer account. Not publicly accessible without a Navan account.
- **Authentication:** API key-based (customer must request API access from Navan account team)
- **Data access:** Trip bookings, expense transactions, user data
- **Integration partners:** Navan integrates with Veryfi (receipt parsing) to provide standardized JSON with "over 110 fields" for expense extraction
- **Booking data integration:** Navan provides booking data export through a dedicated integration, with fields covering flight, hotel, and car rental bookings

> ⚠️ **UNCERTAIN:** Navan's public API documentation is not freely accessible — it requires a provisioned account. Field-level JSON schema for Navan's expense and travel APIs could not be verified from publicly available primary sources. The information above is drawn from Navan's own help center articles and third-party integration documentation (Stitchflow, Supergood.ai), which may not be current or complete.

**What is known about Navan data structure:**
- Navan pre-populates expense categories from booking data (airline, hotel, car rental auto-tagged)
- Export formats include CSV and JSON
- Navan booking data fields include trip type (air/hotel/car/rail), origin, destination, departure date, return date, traveler name, traveler ID, cost center, department
- For ESG purposes, Navan suffers the same distance-absent problem as Concur: booking data provides airport codes (IATA), not distances

> **Sources:**  
> - Navan Help Center: [Navan TMC API Integration Documentation](https://app.navan.com/app/helpcenter/articles/travel/admin/other-integrations/navan-tmc-api-integration-documentation)  
> - Navan Help Center: [Booking Data Integration](https://app.navan.com/app/helpcenter/articles/travel/admin/other-integrations/booking-data-integration)  
> - Supergood.ai: [Navan API](https://docs.supergood.ai/navan-api/)  
> - GitLab Handbook: [Navan Expense End Users Guide](https://handbook.gitlab.com/handbook/business-technology/enterprise-applications/guides/navan-expense-guide/)

---

### 3.11 Emission Factor Mapping — Travel Categories

Once activity data is extracted, emission factors must be applied. Reference datasets:

#### Air Travel (DEFRA 2024/2025 methodology)

| Flight Type | Class | Emission Factor | Source |
|---|---|---|---|
| Short-haul (<3,700 km) | Economy | ~0.158 kg CO2e/passenger-km | DEFRA 2024 (⚠️ exact value — verify against current DEFRA release) |
| Long-haul (>7,400 km) | Economy | ~0.147 kg CO2e/passenger-km | DEFRA 2024 |
| Long-haul | Business | ~0.429 kg CO2e/passenger-km | DEFRA 2024 (business class ~2.9× economy) |
| Long-haul | First | ~0.588 kg CO2e/passenger-km | DEFRA 2024 (first class ~4× economy) |

> ⚠️ **UNCERTAIN:** DEFRA updates emission factors annually. The 2025 update introduced significant reductions (16–42% across flight categories) compared to 2024, due to correction of COVID-era low load factor data. Values above are approximate and must be sourced from the current DEFRA/DESNZ Greenhouse Gas Conversion Factors publication.

**Class of service mapping from Concur IATA codes:**
```
Y = Economy class
W = Premium economy (⚠️ not always a separate factor in DEFRA — often averaged with economy)
C = Business class
F = First class
```

#### Hotel Stays

| Region | Emission Factor | Source |
|---|---|---|
| UK | ~20.8 kg CO2e per room night | DEFRA 2024 (⚠️ verify) |
| US (average) | ~26.0 kg CO2e per room night | EPA / GHG Protocol |
| Global average | ~18–35 kg CO2e per room night (wide range) | HCMI / Cornell |

> ⚠️ **UNCERTAIN:** Hotel emission factors vary enormously by property, country, and energy source. GHG Protocol Category 6 guidance recommends location-specific factors where available. The figures above are representative averages only.

#### Car Rental / Ground Transport

| Vehicle Type | Emission Factor | Source |
|---|---|---|
| Average rental car (gasoline) | ~0.17–0.21 kg CO2e/km | DEFRA 2024 |
| Taxi/rideshare | ~0.15–0.21 kg CO2e/km | DEFRA 2024 |
| Electric vehicle rental | ~0.05–0.10 kg CO2e/km (grid dependent) | DEFRA 2024 |

> **Sources:**  
> - GHG Protocol: [Scope 3 Category 6 Technical Guidance](https://ghgprotocol.org/sites/default/files/standards_supporting/Chapter6.pdf)  
> - DEFRA (via SQUAKE): [Flight: DEFRA — July 2024 Update](https://docs-integration.squake.earth/notable-changes/calculation-changes/flight-defra-july-2024)  
> - Thrust Carbon: [2025 DEFRA Emissions Factors Update](https://thrustcarbon.com/insights/2025-defra-emissions-factors-update-2)  
> - SQUAKE: [How to Calculate Scope 3.6 Emissions](https://www.squake.earth/blog/how-to-calculate-scope-3-6-emissions-from-business-travel)  
> - Avarni: [How to calculate Scope 3 emissions associated with business travel flights](https://www.avarni.co/news/how-to-calculate-scope-3-emissions-associated-with-business-travel-flights)

---

### 3.12 What Would Break in a Real Deployment

1. **No distance for flights — only airport codes.** The Concur Expense API does not provide flight distances. The Itinerary API provides origin and destination airport codes, from which distances must be computed. An ESG pipeline that relies only on expense data (without itinerary access) cannot perform distance-based calculations for air travel and must fall back to spend-based methods, which are significantly less accurate (spend-based method carries wide uncertainty bands and is sensitive to ticket price volatility).

2. **Expense type code variability.** `AIRFR`, `LODNG`, `CARRT` are common defaults but are configurable per Concur tenant. An organization may have renamed "Airfare" to "Air Travel" with code `AIRFL`, or split lodging into "Domestic Hotel" and "International Hotel" with different codes. The ESG platform must retrieve the expense type mapping from each tenant's configuration via the Expense Group Configurations API — it cannot hardcode type codes.

3. **Multi-leg trips appearing as one expense.** A round-trip ticket purchased as one transaction appears as one expense entry (`AIRFR`, amount = total round-trip cost). The `Comment` field may say "NYC-LHR-NYC" or nothing at all. Computing per-leg emissions requires either itinerary data (which has per-segment records) or assuming symmetric routing.

4. **Missing itinerary linkage.** Not all travel booked outside Concur Travel (e.g., directly on airline websites, via corporate cards without GDS data) will have an itinerary record. Expense entries exist (the credit card charge is imported), but there is no corresponding `Booking` record with flight segments. For these "orphan" expenses, the ESG platform has only vendor name and amount — insufficient for distance-based calculation.

5. **Class of service ambiguity.** The IATA class code `W` (premium economy) is not always a separate factor in DEFRA tables (it may be averaged with economy or treated as economy). Organizations with significant premium economy travel will see systematic understatement if the platform maps W→Y.

6. **Navan API access gating.** Unlike Concur which has a public developer program, Navan's API requires account provisioning. This creates an onboarding friction point: the ESG platform cannot connect to Navan until the customer's Navan account team enables API access, which can take days to weeks and requires the customer to own the provisioning process.

7. **Currency and reimbursement mixing.** Concur expense reports can contain entries in multiple currencies (employee booked in GBP, company card charged in USD, per-diem in EUR). The `TransactionCurrencyCode` field exists but the ESG platform must handle multi-currency normalization consistently. Using `PostedAmount` (which may be in reporting currency) rather than `TransactionAmount` avoids some FX issues but must be done deliberately.

8. **DEFRA factor vintage mismatches.** The 2025 DEFRA factors for flights are 16–42% lower than 2024 factors. If an ESG platform has a large library of historical travel data and applies current-year factors retroactively, it will misrepresent historical emissions. Factors must be vintage-matched to the year of travel, not the year of reporting.

---

## Summary Cross-Source Risks

| Risk | SAP | Utility | Travel |
|---|---|---|---|
| Unit/scale normalization | High (L vs LT, M3 vs KG) | High (kWh vs Wh, powerOfTenMultiplier) | Medium (miles vs km for mileage) |
| Period misalignment | Medium (BUDAT vs BLDAT) | High (billing cycle vs calendar month) | Low (booking date is explicit) |
| Missing key field | Medium (no plant name in IDoc) | Low (meter ID present) | High (no distance in expense entry) |
| Schema variability | Low (IDoc is fixed) | High (CSV varies by utility) | High (expense type codes vary by tenant) |
| Estimated/provisional data | Low | High (estimated reads) | Low |
| Data access gating | Low (IDoc delivered by push) | Low (download available) | High (Navan requires provisioning) |
| Lookup table dependency | High (WERKS→T001W, MATNR→MARA) | Medium (meter ID→facility) | High (IATA code→lat/lon→distance) |

---

*End of Research Notes*

---

## Primary Sources Referenced

- SAP Help Portal: [Purchase Orders Using IDocs, BAPI, or OData API](https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/9905622a5c1f49ba84e9076fc83a9c2c/e69ce957fc00be12e10000000a4450e5.html)
- SAP API Business Hub: [Material Documents - Read, Create](https://api.sap.com/api/API_MATERIAL_DOCUMENT_SRV/resource)
- SAP4TECH: [SAP Good Movement IDoc (MBGMCR03)](https://sap4tech.net/sap-good-movement-idoc/)
- se80.co.uk: [MBGMCR03](https://www.se80.co.uk/sap-idocs/?name=mbgmcr03)
- leanx.eu: [EKPO](https://leanx.eu/en/sap/table/ekpo.html), [EKKO](https://leanx.eu/en/sap/table/ekko.html), [MSEG](https://leanx.eu/en/sap/table/mseg.html)
- beyondse16.com: [EKPO field list](https://beyondse16.com/2020/04/16/complete-field-list-of-sap-table-ekpo-purchasing-document-item/)
- SAP Blog: [Dealing with unit of measure in purchasing document and goods movement](https://blogs.sap.com/2015/06/24/dealing-with-unit-of-measure-in-purchasing-document-and-goods-movement/)
- SAP Community: [SAP Good Movement Types](https://community.sap.com/t5/technology-blog-posts-by-members/sap-good-movement-types-list-of-sap-movement-types/ba-p/13551698)
- Green Button Alliance: [Green Button Standard](https://www.greenbuttonalliance.org/green-button)
- Green Button Alliance: [Utility Bill Data Mapping](https://www.greenbuttonalliance.org/utility-bill-data)
- UtilityAPI: [Green Button XML Format](https://utilityapi.com/docs/greenbutton/xml)
- Green Button Dev: [ESPI XSD](https://dev.greenbuttonalliance.org/espiusecase.html)
- US DOE: [Green Button](https://www.energy.gov/data/green-button)
- wiki.dlma.com: [Analyzing PG&E data with VisiData](https://wiki.dlma.com/vd-pge)
- SAP Concur Developer Center: [Reports v3](https://developer.concur.com/api-reference/expense/expense-report/v3.reports.html)
- SAP Concur Developer Center: [Reports v4](https://developer.concur.com/api-reference/expense/expense-report/v4.reports.html)
- SAP Concur Developer Center: [Expense Entries v3](https://developer.concur.com/api-reference/expense/expense-report/expense-entry.html)
- SAP Concur Developer Center: [Booking Resource](https://developer.concur.com/api-reference/travel/itinerary/booking/booking-resource.html)
- SAP Concur Developer Center: [Expense Group Configurations](https://developer.concur.com/api-reference/expense/expense-report/expense-group-configurations.html)
- SAP Concur Community: [CO2 mileage capture](https://community.concur.com/t5/Concur-Expense-Forum/Other-transportation-mileage-capture-for-CO2-reporting/m-p/81873)
- Thrust Carbon: [SAP Concur Integration](https://thrustcarbon.com/thrust-carbon-business-intelligence-tools-for-sap-concur-customers)
- Navan Help Center: [TMC API Integration](https://app.navan.com/app/helpcenter/articles/travel/admin/other-integrations/navan-tmc-api-integration-documentation)
- GHG Protocol: [Scope 3 Category 6](https://ghgprotocol.org/sites/default/files/standards_supporting/Chapter6.pdf)
- SQUAKE: [DEFRA July 2024 Flight Update](https://docs-integration.squake.earth/notable-changes/calculation-changes/flight-defra-july-2024)
- Thrust Carbon: [2025 DEFRA Update](https://thrustcarbon.com/insights/2025-defra-emissions-factors-update-2)
- Avotechs: [OData APIs vs IDocs](https://avotechs.com/blog/odata-apis-vs-idocs/)
- Heiko Evermann: [SAP IDoc tables](https://heikoevermann.com/sap-idoc-which-tables-are-used-to-store-it/)
