# Estaciones de telefonía móvil

Proyecto para descargar las estaciones de telefonía móvil que intersectan la
superficie de `data/input/selected_area.kml`, consultar sus detalles técnicos
y visualizarlas en un mapa interactivo.

## Estructura

```text
data/
  input/
    selected_area.kml             Polígono de cobertura de entrada
  processed/
    cellular_sites.csv            Estaciones obtenidas del Geoportal
    cellular_site_details.csv     Bandas y niveles medidos por estación
output/
  cellular_sites_map.html         Mapa interactivo generado
scripts/
  download_cell_sites.py          Descarga estaciones por teselas (zoom 7)
  download_cell_site_details.py   Consulta los detalles de cada estación
  create_cell_sites_map.py        Genera el mapa HTML
```

Los CSV existentes se incluyen como una instantánea de los datos descargados.
Los scripts pueden volver a generarlos y sobrescribirlos.

## Requisitos

- Python 3.10 o posterior.
- No hacen falta paquetes externos de Python.
- Se necesita Internet para consultar el Geoportal y para cargar las teselas
  del mapa interactivo.

## Uso

Ejecuta estos comandos desde la carpeta raíz del proyecto:

```powershell
python .\scripts\download_cell_sites.py
python .\scripts\download_cell_site_details.py
python .\scripts\create_cell_sites_map.py
```

El segundo comando guarda avances en un CSV parcial para poder reanudar la
descarga si se interrumpe. Para controlar el número de consultas simultáneas:

```powershell
python .\scripts\download_cell_site_details.py --workers 2
```

El mapa selecciona 350 ubicaciones únicas de forma reproducible. Para cambiar
la muestra:

```powershell
python .\scripts\create_cell_sites_map.py --count 350 --seed 42
```

Abre `output/cellular_sites_map.html` en un navegador. Las estaciones con las
mismas coordenadas se muestran como un único punto; el pop-up incluye los
registros de operadores y las frecuencias asignadas disponibles.

## Fuentes

- Geoportal del Ministerio: `https://geoportal.minetur.gob.es/VCTEL/`
- Mapa base: OpenStreetMap o Carto, seleccionable en el propio mapa.

Respeta las condiciones de uso y atribución de los servicios consultados.
