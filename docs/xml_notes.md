# Notatki do XML – `FS25_UrsusC360/modDesc.xml` i `ursusC360.xml`

Gry nie da się tu uruchomić, więc każdy tag/atrybut pochodzi ze źródeł FS25 (nie z pamięci FS19/FS22),
a `tools/validate_mod.py` sprawdza pliki statycznie (XSD, węzły, pliki, orientacje w i3d).

## Źródła

| Skrót | Źródło |
|---|---|
| **XSD** | Oficjalne schematy FS25 generowane z zarejestrowanych ścieżek gry: `https://validation.gdn.giants-software.com/xml/fs25/vehicle.xsd`, `modDesc.xsd`. Oba pliki przechodzą walidację (typy, enumeracje, wymagany `<annotation>`). |
| **LUADOC** | GDN LUADOC FS25 (Script v1.20.0.0), klasy: Motorized, Wheels, Wheel, WheelPhysics, WheelVisual, Drivable, Enterable, VehicleCamera, VehicleCharacter, Lights, AttacherJoints, PowerTakeOffs, Dashboard, Cylindered, FillUnit, SoundManager, Honk, AIDrivable. |
| **dataS** | Rozpakowany dataS FS25 1.21.1.0 (`github.com/maxkra1985/FarmingSimulator25_dataS`): `scripts/main.lua`, `scripts/mods.lua`, `vehicleTypes.xml`, `brands.xml`, `storeCategories.xml`, `l10n/*`, `BrandManager.lua`, `ClassUtil.lua`, `I3DUtil.lua`, `AttacherJointTopArm.lua`, `Dashboard.lua`, `Vehicle.lua`, `Motorized.lua`; oraz skrypty FS25 z `github.com/Dukefarming/FS25-lua-scripting` (`VehicleMotor.lua`, `Vehicle.lua`). |
| **Vanilla** | Kopie plików gry bazowej FS25 (`github.com/jason0611/FS25_DashboardLive_VanillaVehicles`): `claas/arion550/arion550.xml` + `arion550.i3d`, `volvo/fh16/fh16.xml`. |
| **Dane** | Tabela prędkości z instrukcji C-360 (cytowana na agrofoto.pl i agrel.pl), dane silnika S-4003 z zadania. |

## Bloki

| Blok | Źródło | Decyzje |
|---|---|---|
| `modDesc descVersion="94"` | dataS `main.lua`: `g_minModDescVersion = 90`, `g_maxModDescVersion = 111` (1.21.1.0); `mods.lua` sprawdza tylko zakres, nic więcej od wersji nie zależy. Starsze patche miały niższe maksimum (94 we wczesnym zrzucie z 2025, 104 później). | 94 jest przyjmowane przez 1.21 i przez patche od początku 2025; wyższe wartości (np. 111) odrzuciłyby nieaktualne instalacje. |
| `modDesc brands` | dataS `mods.lua` (rejestracja `modDesc.brands.brand`), `BrandManager.addBrand`, `ClassUtil.getIsValidIndexName` (`[%w_.]`). | Gra nie ma marki URSUS (261 marek w `brands.xml`), więc własna `URSUSC360`. |
| `vehicle type="tractor"` | dataS `vehicleTypes.xml`: `tractor` (parent `baseDrivable`, 54 specjalizacje: wheels, motorized, drivable, enterable, lights, attacherJoints, powerTakeOffs, cylindered, fillUnit, dashboard, honk, suspensions, washable, wearable, AI…). | — |
| `storeData` | XSD; Vanilla Arion (`category tractorsS`, `function_tractor`); dataS `storeCategories.xml`, `l10n_en.xml`. | Cena 28000, lifetime 600. |
| `base` (`size`, `components`, `schemaOverlay`, `mapHotspot`, `speedLimit`) | XSD, Vanilla Arion; `Vehicle.lua` (`speedLimit` = limit roboczy, dla ciągnika `doCheckSpeedLimit()` = false – tylko informacja w sklepie). | Masa 2340 kg + koła 0,36 t ≈ 2,70 t; COM `0 0.78 -0.25` → ok. 36/64 % na osie. |
| `wheels` | LUADOC `Wheel:loadFromXML` (repr wymagany, driveNode ≠ repr, driveNode bez rotacji), `WheelPhysics:loadFromXML` (radius/width wymagane, `tireType` domyślnie `mud`, `spring × Vehicle.SPRING_SCALE (10)`), `WheelVisual` (bez `filename` nie ładuje wizuali), `Wheels:loadAckermannSteeringFromXML`. | Brak `dimensions`/`filename`/`tireCategories` → zostają nasze modele z i3d. Sprężyny z obciążeń statycznych: przód 0,5 t, tył 0,9 t, ugięcie opony 35 % skoku; hamulce tylko na tylnych kołach (`brakeFactor 0` z przodu). Ackermann 38°, środek między kołami 3–4. |
| `motorized` motor | LUADOC `Motorized:loadMotor` (torque `rpm`+`torque` w kNm), `loadGears` (`maxSpeed` = km/h przy `maxRpm`), `loadGearGroups` (`ratio` = mnożnik prędkości, zapisywane jako 1/ratio), dataS `VehicleMotor.lua` (grupy, `directionChange useGear` przełącza na `backwardGear`). Vanilla FH16 (`directionChange useGear="true"` + backwardGear). | maxRpm 2200 (znamionowe), idle 700, 10F/2R z fabrycznej tabeli, reduktor 0,2339. Wolne 2350 obr/min nie jest modelowane – pętla 2300 gra przy 2200 z pitch 0,957. `ptoMotorRpmRatio 3.69` (541,6 obr/min WOM przy 2000). |
| `consumers` | LUADOC `Motorized:updateConsumers` (usage [l/h] × współczynnik trudności 1,0/1,5/2,5 × obciążenie). | `usage 12` = 12 l/h przy pełnym obciążeniu na „niskim” zużyciu; tylko diesel (bez DEF/powietrza). |
| `differentials` | LUADOC `Motorized:loadDifferentials`. | Tylko tylna oś (3–4), `maxSpeedRatio 1.6` (ciasne skręty ~1,5). Forma z `differentialConfigurations`, bo forma bez konfiguracji ma podwójne zagnieżdżenie `differentials.differentials`. |
| `exhaustEffects`, `exhaustFlap` | LUADOC `Motorized:loadExhaustEffects/onUpdate` (efekt linkowany do węzła, oś rury = lokalne +Y; klapka `setRotation` absolutne wokół `rotationAxis`), ścieżka `$data/effects/exhaust/exhaust.i3d` z Vanilla Arion. | Ciemniejszy dym przy wysokich obrotach (starszy diesel). |
| `motorized.sounds` | LUADOC `Motorized:loadSounds`, `SoundManager:loadSampleAttributesFromXML/loadModifiersFromXML/updateSampleModifiers` (modyfikatory różnych typów się mnożą, nieznany typ jest cicho ignorowany), XSD (enumeracja typów), dataS `Motorized.lua:1747` (pętle startują po `motorStart`). | Opis poniżej. |
| `dashboards` (rpm, paliwo) | dataS `Dashboard.lua` (ROT z `rotAxis` podmienia tylko jedną składową Eulera), LUADOC Motorized (`rpm` = obroty rzeczywiste), FillUnit (`fillLevel`). | Obrotomierz 0–2500 obr/min: +135° → −135°; paliwo 0–70 l: +60° → −60° (obrót wokół lokalnego Z, dodatni = przeciwnie do zegara patrząc od kierowcy). |
| `drivable steeringWheel` | LUADOC `Drivable:updateSteeringWheel`: `setRotation(node, 0, rot, 0)`. | 600° wewnątrz, 120° na zewnątrz. |
| `enterable` | LUADOC `VehicleCamera:loadFromXML/update` (zoom wzdłuż początkowego przesunięcia, korekta terenu zakłada kamerę na +Z celu obróconego o 180°), `VehicleCharacter` (łańcuchy `leftArm/rightArm/leftFoot/rightFoot`), Vanilla Arion. | Kamera zewnętrzna: `translation="0 0 7.5" rotation="-18 180 0"` nadpisuje i3d (konwencja GIANTS), orientacja kamery liczona jest „look-at”. |
| `lights` | LUADOC Lights (`realLights.low/high`, `light/brakeLight/turnLightLeft/Right`), Vanilla Arion. | Stany: 0 (drogowe) i 0+1 (robocze tył). |
| `attacherJoints` | LUADOC AttacherJoints (`rotationNode`/`rotationNode2` interpolowane tym samym alfa, `updateAttacherJointRotation` obraca węzeł zaczepu wokół lokalnego Z o −lerp(`upper/lowerRotationOffset`), `distanceToGround` wymagane przy rotationNode), dataS `AttacherJointTopArm.lua` (+Z łącznika celuje w narzędzie, w spoczynku rotacja 0 180 0 przy `zScale -1`), Vanilla Arion i3d (węzły zaczepów mają rotację `0 90 0`). | TUZ: rotationNode = `bottomArm`, rotationNode2 = `liftArm`; kąty i wysokości policzone z geometrii (niżej). `attacherJointBack` jest dzieckiem `bottomArm`, więc `lower/upperRotationOffset` = kąty `bottomArm` – narzędzie nie przechyla się przy podnoszeniu (Arion robi to węzłem `Rot2`). Bez `<bottomArm>` (funkcja celowania ramion przestawiałaby węzeł niosący zaczep). |
| `powerTakeOffs` | LUADOC `PowerTakeOffs:attachTypedPowerTakeOff` (wał wzdłuż +Z węzła wyjściowego). | Wyjście dla TUZ i obu zaczepów (`attacherJointNodes`). |
| `cylindered` | LUADOC `Cylindered` (movingPart: +Z celuje w referencePoint, `scaleZ`), AttacherJoints (brudzi tylko `bottomArm.rotationNode`). | Wieszaki z `isActiveDirty="true"`, bo obrót zaczepu nie odświeża movingParts. |
| `fillUnit` | XSD, Vanilla Arion. | 70 l diesla, wskaźnik paliwa. |
| `honk`, `ai`, `foliageBending`, `wearable`, `washable`, `suspensions` | LUADOC Honk (pętla przy trzymaniu), AIDrivable (`frontWheelIndices`), dataS `AIImplement.lua` (`collisionTrigger#useSize` tworzy pole kolizji AI z `base/size`), XSD, Vanilla Arion. | Kołysanie tułowia kierowcy (brak amortyzacji kabiny). |
| `<!-- I3D_MAPPINGS -->` | `tools/build_mod.py` dopisuje znacznik końca i wstawia `build/i3dMappings.xml`. | — |

## Dźwięk silnika (przenikanie obroty × obciążenie)

- 4 pętle bez obciążenia (`motorIdle` 700, `motorNoLoad_1200/1700/2300`) mnożone przez `MOTOR_LOAD` 0→1 : 1→0,
  4 pętle pełnego obciążenia (`motorLoad_1000/1500/2000/2200`) mnożone przez `MOTOR_LOAD` 0→0 : 1→1 (krzywe równej mocy).
- Każda pętla: `pitch = obroty / obroty natywne` (`MOTOR_RPM_REAL`, 500→2500), głośność = okno obrotów o krzywej równej mocy.
  Okna krzyżują się w średniej geometrycznej sąsiednich obrotów natywnych (917, 1428, 1977 / 1225, 1732, 2095),
  suma mocy w całym zakresie 700–2200 obr/min = 0,98–1,00. Wolne obroty grają tylko do ~1050 obr/min.
- Wycie skrzyni: `SPEED` (km/h), pitch ≈ prędkość/20 (natywnie 20 km/h), głośniej pod obciążeniem.
- Zgrzyt (`clutchCracking`) przy zmianie biegu bez sprzęgła, `gearShift` przy włączeniu/wyłączeniu biegu i zmianie grupy,
  `hydraulicPump` w czasie ruchu TUZ, `horn` jako klakson. `motorStart.ogg` kończy się na wolnych obrotach (700 wg
  `sounds.json`) – pętle startują dokładnie po nim, a czas rozruchu (`motorStartDuration`) = długość pliku (4,6 s).
- `sounds/sounds.json` potwierdza obroty natywne wszystkich pętli i zachowaną naturalną głośność (RMS rośnie z obrotami
  i obciążeniem), więc pętle silnika mają głośność 1,0; pompa hydrauliki (−10,9 dBFS RMS) ściszona do 0,5, zgrzyt
  (one-shot) gra raz na zdarzenie (`loops="1"`).

## Geometria TUZ (z `blender/build_ursus_c360.py`)

Oś cięgieł (0,48; −1,12), zaczep (0,40; −1,93), punkt wieszaka (0,44; −1,55), wałek podnośnika (1,13; −1,26),
koniec ramienia (1,06; −1,66), wieszak 0,630 m. Dla wysokości zaczepu 0,25 / 0,95 m: `bottomArm` −10,77° / 40,91°,
z rozwiązania czworoboku `liftArm` −11,52° / 42,24° (liniowa interpolacja myli się < 0,5°). Walidator przelicza to
z i3d i ostrzega, gdy geometria się zmieni.

## Zmiany w i3d (zastosowane)

Punkty 1–5 oraz ustawienia fizyki z sekcji „i3d physics attributes” (filtry kolizji, `clipDistance`, `density`,
węzeł tankowania `exactFillRootNodeFuel`) są zastosowane w `FS25_UrsusC360/ursusC360.i3d`;
`tools/validate_mod.py` zwraca 0 błędów i 0 ostrzeżeń.

1. `steeringWheel` musi mieć rotację 0 0 0 – pochylenie kolumny (−36,87° X) na nowym rodzicu (np. `steeringWheelRot`),
   bo `Drivable` co klatkę zeruje X/Z. Oś Y (wzdłuż kolumny do kierowcy) zostaje.
2. `attacherJointBack`, `attacherJointTrailerLow`, `attacherJointTrailer`: rotacja **0 90 0** (lokalne X do tyłu, Y w górę, Z w lewo),
   jak w Vanilla Arion; inaczej narzędzia doczepią się obrócone o 90°, a limity obrotu trafią w złe osie.
3. `topArm`: rotacja 0 180 0 (+Z do tyłu), `topArmTranslation` na lokalnym (0, 0, +0,36), `topArmReference` na (0, 0, +0,18).
4. `liftRodLeft/Right` jako dzieci `liftArm` (te same pozycje w świecie), bo `liftArm` jest obracany przez `rotationNode2`.
5. `rpmNeedle`/`fuelNeedle`: rotacja 0 0 0 pod rodzicem niosącym orientację tarczy (jak `needleRpmRot` w Arionie).

Zalecane: orientacje celów IK dłoni jak w Arionie (lewa ok. −34/−20,6/−130° względem kierownicy, prawa lustrzanie);
`liftArm`, `bottomArm`, `exhaustFlap`, węzły `…Visual` z rotacją 0 0 0 (kąty w XML są względne).

## i3d physics attributes

Źródła: dataS FS25 1.21.1.0 – `scripts/CollisionFlag.lua` (bity), `scripts/CollisionPreset.lua` (presety, z których
GIANTS eksportuje `collisionMaskFlags.xml` dla edytora i eksporterów), `Vehicle:loadComponentFromXML`,
`vehicles/wheels/WheelPhysics.lua`, `specializations/FillUnit.lua`, `specializations/Motorized.lua`,
`triggers/FillTrigger.lua`, `specializations/AIImplement.lua`; trzy pliki gry bazowej zapisane przez GIANTS Editor 10:
`claas/arion550.i3d` (10.0.10), `masseyFerguson/series7S.i3d` (10.0.5), `fendt/mt1100.i3d` (10.0.4) – wszystkie mają
identyczne wartości. Oficjalny schemat i3d (`i3d.giants.ch/schema/i3d-1.6.xsd`) zwraca stronę 404, więc nazwy atrybutów
pochodzą z tych eksportów. W FS25 zamiast `collisionMask` z FS22 są `collisionFilterGroup` (czym węzeł jest) i
`collisionFilterMask` (z czym koliduje), zapisywane szesnastkowo.

| Węzeł | Atrybuty i3d (dokładnie jak w eksportach GE10) | Preset FS25 |
|---|---|---|
| (a) komponent `ursusC360_main_component1` (Shape, korzeń sceny) | `dynamic="true" compound="true" collisionFilterGroup="0x10004" collisionFilterMask="0xfe3ffb83" clipDistance="300" nonRenderable="true"` | `VEHICLE` |
| (b) kolizje `collisionCab`, `collisionFenderLeft/Right`, `collisionRear` (Shape, potomkowie komponentu) | `compoundChild="true" collisionFilterGroup="0x10004" collisionFilterMask="0xfe3ffb83" density="0.001" nonRenderable="true"` – bez `static/dynamic/kinematic` | `VEHICLE` |
| (c) cel tankowania `exactFillRootNodeFuel` (nowy Shape, dziecko komponentu) | `kinematic="true" compound="true" collisionFilterGroup="0x40000000" collisionFilterMask="0x20000000" nonRenderable="true"` | `EXACT_FILL_ROOT_NODE` |

- `0x10004` = bit 2 `CAMERA_BLOCKING` + bit 16 `VEHICLE`. `0xfe3ffb83` = wszystkie bity oprócz `CAMERA_BLOCKING`,
  `GROUND_TIP_BLOCKING`, `PLACEMENT_BLOCKING`, `AI_BLOCKING`, `PRECIPITATION_BLOCKING`, `TERRAIN_DISPLACEMENT`,
  `ANIMAL_POSITIONING`, `ANIMAL_NAV_MESH_BLOCKING`, `TRAFFIC_VEHICLE_BLOCKING` (objętości pomocnicze map).
- `Vehicle:loadComponentFromXML` ostrzega, gdy grupa lub maska komponentu nie ma bitu `VEHICLE`. Wczesny eksport
  zapisywał `0x2000` / `0xfffffbff` (w FS25 bit 13 to `ROAD`: ciągnik bez `VEHICLE` i `CAMERA_BLOCKING`, niewidoczny dla
  wyzwalaczy i raycastów filtrujących po `VEHICLE`, np. pola kolizji AI `AI_BLOCKING|PLAYER|TREE|VEHICLE`). Obecny
  eksport (`blender/export_i3d.py`, `COLLISION`) zapisuje `0x10004` / `0xfe3ffb83`, `clipDistance="300"` na
  komponencie i `density="0.001"` na dzieciach.
- Komponent – czego nie dawać do i3d: masa, środek masy, `inertiaScale` i `solverIterationCount` przychodzą z XML
  (`setMass` tylko dla komponentu dynamicznego, `setCenterOfMass`, `setSolverIterationCount`), więc `density` komponentu
  nie ma znaczenia (eksporty GE10 go nie mają). Tłumienia zostawić domyślne (dev-ostrzeżenie przy liniowym > 0,01,
  kątowym > 0,05 lub < 0,0001). Bez `static`/`kinematic`; na klientach MP gra sama zmienia `dynamic` na kinematyczny.
  Translacja Y pierwszego komponentu musi wynosić 0. Brak `clipDistance` → ostrzeżenie i 300.
  `density="0.001"` na dzieciach: tak robią wszystkie trzy pliki gry; wniosek (niezweryfikowany): dzieci prawie nie
  wpływają na rozkład masy/bezwładności, więc decyduje masa i środek masy z XML.
- (c) Wyzwalacze: żaden nie jest potrzebny w i3d. Pole kolizji AI tworzy `AIImplement:loadAICollisionTriggerFromXML` z
  `<ai><collisionTrigger useSize="true"/>` na podstawie `base/size` (dodane do XML), wsiadanie działa na odległość
  (`enterReferenceNode#interactionRadius`). Potrzebny jest natomiast **cel tankowania**: `FillTrigger.TRIGGER_MASK =
  CollisionFlag.FILLABLE` (dystrybutory i zbiorniki paliwa widzą tylko kształty z bitem `FILLABLE` w grupie),
  `FillUnit` rejestruje tylko `exactFillRootNode` z bitem `FILLABLE` (inaczej ostrzeżenie i pominięcie), a `Motorized`
  loguje „Missing exactFillRootNode for fuel fill unit”. Bez niego ciągnika nie da się zatankować.
  Węzeł `exactFillRootNodeFuel` (w kontrakcie `docs/i3d_nodes.md`): Shape z prostopadłościanem 0,30 × 0,20 × 0,30 m,
  środek (x = −0,14, y = 1,40, z = 0,30) nad korkiem wlewu paliwa na masce, rotacja 0 0 0, dziecko
  `ursusC360_main_component1`, atrybuty z tabeli (c); w XML jednostka paliwa (`fillUnit` diesel) zawiera
  `<exactFillRootNode node="exactFillRootNodeFuel"/>`. Walidator ostrzega, gdyby go zabrakło.
- Koła: fizyka kół nie potrzebuje niczego w i3d – `WheelPhysics` tworzy kształt koła skryptem (`createWheelShape` na
  komponencie, grupa `VEHICLE`, maska `0x7e3fff83` = wszystko oprócz `WATER` i objętości pomocniczych; bez
  `TERRAIN_DISPLACEMENT`, gdy koło nie zapada się w teren). W i3d wystarczą: `repr` (TransformGroup w środku koła) wewnątrz
  komponentu dynamicznego (`Wheel:loadFromXML`: „Needs to be a child of a collision”), dziecko `…Visual` z rotacją 0 0 0 i
  siatki opon bez żadnych flag fizyki. Kolizje nadwozia nie mogą sięgać gruntu pod kołami (obecny prostopadłościan
  komponentu zaczyna się na y = 0,41 m – w porządku).
- `tools/validate_mod.py` sprawdza wszystkie powyższe reguły (błąd przy braku bitu `VEHICLE`/`FILLABLE`, ostrzeżenie przy
  odstępstwie od presetu, braku `clipDistance`, gęstości dzieci ≠ 0,001, starym atrybucie `collisionMask`, zbędnych
  wyzwalaczach i bryłach sztywnych).

## Niepewności

- Proporcje głośności (silnik / skrzynia / hydraulika) ustawione z pomiarów RMS w `sounds.json`, nie odsłuchane w grze.
  `seam_step_ratio` w `sounds.json` to skok próbek na szwie pętli podzielony przez największy skok w całym pliku; wartości
  < 1 oznaczają, że szew nie odstaje. Dla `gearboxWhine`, `hydraulicPump` i `horn` (0,4–0,98) wynik wynika z ostrych
  zboczy samego przebiegu, a pętle są z konstrukcji ciągłe (całkowita liczba okresów, filtracja kołowa).
- `SPEED` = km/h wynika z użycia w plikach gry (rejestracja typów modyfikatorów nie jest widoczna w LUADOC).
- Sprężyny/tłumienie opon, siła hamowania (5) i `accelerationLimit` (1,5) są wyliczone, nie przetestowane w grze.
- Wahliwa oś przednia nie jest animowana (wymagałaby osi `frontAxle` z +Z w bok); obroty jałowe 2350 i blokada mechanizmu różnicowego nie są modelowane.
- Brak osobnych świateł drogowych (typ 3) – kontrakt ma tylko jedną parę reflektorów.
- Nazwy szablonów dźwięków gry nie są wymienione w XSD, dlatego mod używa wyłącznie własnych plików (bez deszczu w kabinie).
- Kolizja zaczepów górnego i dolnego z TUZ nie jest blokowana (`disabledByAttacherJoints` nieużyte).
