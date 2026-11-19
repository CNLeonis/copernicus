import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

const OPOLE = [50.675, 17.921]
const serviceStyles = {
  shop: { label: 'Sklepy spożywcze', color: '#d55e00' },
  school: { label: 'Szkoły', color: '#7851a9' },
  bus_stop: { label: 'Przystanki', color: '#008060' },
}
const pointIcon = L.divIcon({
  className: 'selected-pin',
  html: '<span></span>',
  iconSize: [30, 38],
  iconAnchor: [15, 38],
})
const stopIcon = L.divIcon({
  className: 'bus-stop-symbol',
  iconSize: [24, 18],
  iconAnchor: [12, 9],
})

export default function LocationMap({ point, onSelect, radius = 500, boundary, cell }) {
  const container = useRef(null)
  const mapRef = useRef(null)
  const markerRef = useRef(null)
  const onSelectRef = useRef(onSelect)
  const [tileError, setTileError] = useState(false)
  const [serviceError, setServiceError] = useState(false)
  onSelectRef.current = onSelect

  useEffect(() => {
    const map = L.map(container.current).setView(OPOLE, 14)
    mapRef.current = map
    const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map)
    tiles.on('tileerror', () => setTileError(true))
    tiles.on('load', () => {
      // A failed tile stays marked as failed until a later successful load.
      const failed = container.current?.querySelector('.leaflet-tile:not(.leaflet-tile-loaded)')
      if (!failed) setTileError(false)
    })
    map.on('click', ({ latlng }) => {
      const location = latlng.wrap()
      onSelectRef.current({ latitude: location.lat, longitude: location.lng })
    })
    const observer = new ResizeObserver(() => map.invalidateSize())
    observer.observe(container.current)
    return () => {
      observer.disconnect()
      map.remove()
      mapRef.current = null
      markerRef.current = null
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    const map = mapRef.current
    const layer = L.layerGroup().addTo(map)
    const points = L.layerGroup()
    const buildings = L.layerGroup()
    const buildingTooltips = new Map()
    function updateZoom() {
      const detailed = map.getZoom() >= 16
      layer.removeLayer(detailed ? points : buildings)
      layer.addLayer(detailed ? buildings : points)
    }
    map.on('zoomend', updateZoom)
    updateZoom()
    async function loadServices() {
      try {
        let offset = 0
        let total
        do {
          const response = await fetch(`/api/services?include_buildings=true&limit=5000&offset=${offset}`, {
            signal: controller.signal,
          })
          if (!response.ok) throw new Error('Services unavailable')
          const data = await response.json()
          if (controller.signal.aborted) return
          for (const feature of data.features) {
            const style = serviceStyles[feature.properties.category]
            if (!style) continue
            const [lon, lat] = feature.geometry.coordinates
            const tooltip = document.createElement('span')
            tooltip.textContent = `${style.label}: ${feature.properties.name || 'bez nazwy'}`
            if (feature.properties.category === 'bus_stop') {
              L.circleMarker([lat, lon], {
                radius: 4, color: '#fff', weight: 1,
                fillColor: style.color, fillOpacity: 1,
              }).bindTooltip(tooltip.cloneNode(true)).addTo(points)
              L.marker([lat, lon], { icon: stopIcon, keyboard: false, bubblingMouseEvents: true })
                .bindTooltip(tooltip).addTo(buildings)
              continue
            }
            const marker = L.circleMarker([lat, lon], {
              radius: 5, color: '#fff', weight: 1,
              fillColor: style.color, fillOpacity: 1,
            }).bindTooltip(tooltip)
            const geometry = feature.properties.building_geometry
            if (!geometry) {
              marker.addTo(layer)
              continue
            }
            marker.addTo(points)
            const key = JSON.stringify([feature.properties.category, geometry])
            const existingTooltip = buildingTooltips.get(key)
            if (existingTooltip) {
              existingTooltip.textContent += `; ${feature.properties.name || 'bez nazwy'}`
            } else {
              const buildingTooltip = tooltip.cloneNode(true)
              buildingTooltip.textContent += ' (budynek z usługą)'
              buildingTooltips.set(key, buildingTooltip)
              L.geoJSON(geometry, {
                style: { color: style.color, weight: 2, fillColor: style.color, fillOpacity: 0.35 },
              }).bindTooltip(buildingTooltip).addTo(buildings)
            }
          }
          offset += data.features.length
          total = data.total
          if (!data.features.length && offset < total) throw new Error('Incomplete services')
        } while (offset < total)
        setServiceError(false)
      } catch {
        if (!controller.signal.aborted) {
          layer.clearLayers()
          setServiceError(true)
        }
      }
    }
    loadServices()
    return () => { controller.abort(); map.off('zoomend', updateZoom); layer.remove() }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !point) return
    const position = [point.latitude, point.longitude]
    if (markerRef.current) markerRef.current.setLatLng(position)
    else markerRef.current = L.marker(position, {
      icon: pointIcon, title: 'Wybrany punkt', alt: 'Wybrany punkt',
    }).addTo(map)
    map.setView(position, Math.max(map.getZoom(), 14))
  }, [point])

  useEffect(() => {
    if (!mapRef.current || !point) return
    const circle = L.circle([point.latitude, point.longitude], {
      radius, color: '#135ab5', weight: 2, fillOpacity: 0.08, interactive: false,
    }).addTo(mapRef.current)
    return () => circle.remove()
  }, [point, radius])

  useEffect(() => {
    if (!mapRef.current || !boundary) return
    const layer = L.geoJSON(boundary, {
      style: { color: '#444', weight: 2, fillOpacity: 0 }, interactive: false,
    }).addTo(mapRef.current)
    mapRef.current.fitBounds(layer.getBounds())
    return () => layer.remove()
  }, [boundary])

  useEffect(() => {
    if (!mapRef.current || !cell) return
    const layer = L.geoJSON(cell, {
      style: { color: '#d07800', weight: 3, fillOpacity: 0.06 }, interactive: false,
    }).addTo(mapRef.current)
    return () => layer.remove()
  }, [cell])

  function selectCenter() {
    const location = mapRef.current.getCenter().wrap()
    onSelect({ latitude: location.lat, longitude: location.lng })
  }

  return (
    <div className="map-shell">
      <div ref={container} className="map" role="region" aria-label="Mapa Opola" aria-describedby="map-help" />
      <div className="map-legend" aria-label="Legenda mapy">
        <strong>Legenda</strong>
        <ul>
          {Object.entries(serviceStyles).map(([key, style]) => <li key={key}>
            <span className={key === 'bus_stop' ? 'legend-stop' : 'legend-building'} style={{ backgroundColor: style.color }} aria-hidden="true" />
            {style.label}
          </li>)}
          <li><span className="legend-radius" aria-hidden="true" />Zasięg analizy</li>
          <li><span className="legend-cell" aria-hidden="true" />Komórka modelu</li>
        </ul>
        <small>Przy oddaleniu: punkty usług.<br />Po przybliżeniu: obrysy budynków i prostokąty przystanków.</small>
        {serviceError && <p role="alert">Nie udało się pobrać punktów usług.</p>}
      </div>
      <div className="map-toolbar">
        <button type="button" className="map-button" onClick={() => mapRef.current.setView(OPOLE, 14, { animate: false })}>Wróć do Opola</button>
        <button type="button" className="map-button" onClick={selectCenter}>Wybierz środek mapy</button>
      </div>
      {tileError && <p className="map-error" role="status">Nie udało się pobrać części mapy. Sprawdź połączenie z internetem.</p>}
    </div>
  )
}
