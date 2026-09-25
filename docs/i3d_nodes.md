# Kontrakt węzłów i3d – `FS25_UrsusC360/ursusC360.i3d`

Plik i3d jest generowany skryptem Blendera (`blender/`). Każdy węzeł z tej listy istnieje
dokładnie raz, a build wstrzykuje do `ursusC360.xml` blok `<i3dMappings>` z `id == nazwa węzła`,
więc XML odwołuje się do węzłów wyłącznie po nazwie.

## Układ współrzędnych i wymiary

- i3d/GIANTS: metry, **Y w górę, +Z = przód pojazdu, +X = LEWA strona** (lewa ręka kierowcy).
- Początek komponentu: poziom gruntu (y = 0), x = 0, z = 0 w połowie rozstawu osi.
- Rozstaw osi 2.125 m → oś przednia z = +1.0625, oś tylna z = −1.0625.
- Rozstaw kół przód/tył 1.45 m → środki kół x = ±0.725.
- Tył: 14.9-28, promień 0.683 m, szerokość 0.378 m (środek y = 0.683).
- Przód: 6.00-16, promień 0.365 m, szerokość 0.17 m (środek y = 0.365).
- Długość ok. 3.57 m (z od −1.80 do +1.77), szerokość 1.83 m, wysokość z kabiną ok. 2.35 m.
- Masa: 2170 kg (sucha, bez obciążników), ok. 2700 kg gotowy do pracy (kabina + obciążniki).

Węzły bez podanej rotacji mają rotację zgodną z komponentem (brak obrotu).

## Węzły

| Nazwa | Typ | Położenie / orientacja | Rola |
|---|---|---|---|
| `ursusC360_main_component1` | Shape (rigid body dynamic, compound) | korzeń sceny | komponent 1 |
| `collisions` | TransformGroup | dziecko komponentu | kolizje (compoundChild), bez odwołań w XML |
| `wheelFrontLeft` / `wheelFrontRight` | TransformGroup | środek koła przedniego (x = ±0.725, y = 0.365, z = 1.0625) | `repr` koła (skręt + zawieszenie) |
| `wheelBackLeft` / `wheelBackRight` | TransformGroup | środek koła tylnego (x = ±0.725, y = 0.683, z = −1.0625) | `repr` koła |
| `wheelFrontLeftVisual` … `wheelBackRightVisual` | TransformGroup (dziecko repr) | środek koła | opona + felga, `driveNode` (toczenie wokół X) |
| `frontFenderLeft` / `frontFenderRight` | Shape (dziecko repr przedniego) | nad kołem | błotnik przedni skręca z kołem, nie toczy się |
| `frontAxle` | TransformGroup | sworzeń wahliwy osi (x = 0, y ≈ 0.42, z = 1.0625) | wahliwa oś przednia (obrót wokół Z) |
| `steeringWheel` | TransformGroup (dziecko `steeringWheelColumn`, rotacja 0 0 0) | środek piasty; rodzic niesie pochylenie kolumny −36.87° X, więc lokalna oś **Y biegnie wzdłuż kolumny** | kierownica (gra zeruje X/Z co klatkę) |
| `leftHandTarget` / `rightHandTarget` | TransformGroup (dzieci `steeringWheel`) | na wieńcu (godz. 10 i 14) | IK rąk |
| `leftFootTarget` / `rightFootTarget` | TransformGroup | pedał sprzęgła / hamulca | IK stóp |
| `playerSkin` | TransformGroup | biodra kierowcy na siedzisku, przodem do +Z | węzeł postaci |
| `seat` | Shape | siedzisko | — |
| `outdoorCameraTarget` | TransformGroup | (0, 1.5, 0) | punkt obrotu kamery zewnętrznej |
| `outdoorCamera` | Camera (dziecko powyższego) | lokalnie (0, 0, −8), rotacja (0, 180, 0) – patrzy na target | kamera zewnętrzna |
| `indoorCameraTarget` | TransformGroup | głowa kierowcy (≈ siedzisko + 0.75 m) | punkt kamery wewnętrznej |
| `indoorCamera` | Camera (dziecko powyższego) | lokalnie (0, 0, 0), rotacja (0, 180, 0) – patrzy do przodu | kamera wewnętrzna |
| `exitPoint` | TransformGroup | lewa strona, grunt (x ≈ 1.3, z ≈ −0.6) | punkt wysiadania |
| `enterReferenceNode` | TransformGroup | lewe drzwi kabiny | punkt wsiadania |
| `exhaustNode` | TransformGroup | wylot rury wydechowej, lokalna oś Y = oś rury (w górę) | efekt spalin |
| `exhaustFlap` | Shape | zawias klapki na wylocie; dodatni obrót wokół lokalnego X otwiera | klapka wydechu |
| `soundMotor` / `soundExhaust` / `soundCabin` | TransformGroup | blok silnika / wylot wydechu / głowa kierowcy | węzły dźwięku |
| `frontLightLeft` / `frontLightRight` | Light (spot) | w reflektorach, świecą do przodu | światła mijania/drogowe |
| `workLightBack` | Light (spot) | tył dachu kabiny, świeci do tyłu | światło robocze tylne |
| `tailLightLeft` / `tailLightRight` | Light (point, czerwone) | lampy na tylnych błotnikach | światła pozycyjne tył |
| `brakeLightLeft` / `brakeLightRight` | Light (point, czerwone) | lampy na tylnych błotnikach | światła stop |
| `turnLightLeftFront` / `turnLightRightFront` | Light (point, pomarańczowe) | kierunkowskazy przednie | kierunkowskazy |
| `turnLightLeftBack` / `turnLightRightBack` | Light (point, pomarańczowe) | kierunkowskazy tylne | kierunkowskazy |
| `bottomArm` | TransformGroup | oś obrotu dolnych cięgieł TUZ (x = 0), obrót wokół X unosi | `rotationNode` dolnych cięgieł |
| `bottomArmLeft` / `bottomArmRight` | Shape (dzieci `bottomArm`) | dolne cięgła | — |
| `attacherJointBack` | TransformGroup (dziecko `bottomArm`), rotacja 0 90 0 | środek między końcówkami dolnych cięgieł | punkt zaczepu TUZ kat. II |
| `topArm` | TransformGroup, rotacja 0 180 0 (+Z do tyłu) | punkt mocowania łącznika górnego | `rotationNode` łącznika górnego |
| `topArmTranslation` | TransformGroup (dziecko `topArm`) | przesuwna część łącznika (lokalne Z) | `translationNode` |
| `topArmReference` | TransformGroup (dziecko `topArmTranslation`) | oczko łącznika | `referenceNode` |
| `liftArm` | TransformGroup | wałek podnośnika, obrót wokół X | ramiona podnośnika |
| `liftRodLeft` / `liftRodRight` | TransformGroup (dzieci `liftArm`), lokalne +Z do referencji | końce ramion podnośnika | wieszaki (celują w referencje) |
| `liftRodLeftRef` / `liftRodRightRef` | TransformGroup (dzieci `bottomArmLeft/Right`) | punkt mocowania wieszaka na cięgle | referencje wieszaków |
| `ptoBack` | TransformGroup | koniec wałka WOM (y = 0.655), lokalne +Z do tyłu | WOM tylny |
| `attacherJointTrailerLow` | TransformGroup, rotacja 0 90 0 | sworzeń zaczepu wahliwego (y ≈ 0.42) | zaczep dolny (`trailerLow`) |
| `attacherJointTrailer` | TransformGroup, rotacja 0 90 0 | zaczep transportowy górny (y ≈ 0.80) | zaczep górny (`trailer`) |
| `rpmNeedle` / `fuelNeedle` | Shape (dzieci `rpmNeedleRot` / `fuelNeedleRot`, rotacja 0 0 0) | wskazówki zegarów, obrót wokół lokalnego Z; rodzic niesie pochylenie tarczy | dashboard |
| `exactFillRootNodeFuel` | Shape (kinematic compound, `collisionFilterGroup=0x40000000`, maska `0x20000000`, nonRenderable) | nad korkiem wlewu (x = −0.14, y = 1.40, z = 0.30) | cel tankowania (`exactFillRootNode`) |
