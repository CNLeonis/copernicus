import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

const OPOLE = [50.675, 17.921]
const pointIcon = L.divIcon({
  className: 'selected-pin',
  html: '<span></span>',
  iconSize: [30, 38],
  iconAnchor: [15, 38],
})

export default function LocationMap({ point, onSelect }) {
  const container = useRef(null)
  const mapRef = useRef(null)
  const markerRef = useRef(null)
  const onSelectRef = useRef(onSelect)
  const [tileError, setTileError] = useState(false)
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
    const map = mapRef.current
    if (!map || !point) return
    const position = [point.latitude, point.longitude]
    if (markerRef.current) markerRef.current.setLatLng(position)
    else markerRef.current = L.marker(position, {
      icon: pointIcon, title: 'Wybrany punkt', alt: 'Wybrany punkt',
    }).addTo(map)
    map.panTo(position)
  }, [point])

  function selectCenter() {
    const location = mapRef.current.getCenter().wrap()
    onSelect({ latitude: location.lat, longitude: location.lng })
  }

  return (
    <div className="map-shell">
      <div ref={container} className="map" role="region" aria-label="Mapa Opola" aria-describedby="map-help" />
      <div className="map-toolbar">
        <button type="button" className="map-button" onClick={() => mapRef.current.setView(OPOLE, 14, { animate: false })}>Wróć do Opola</button>
        <button type="button" className="map-button" onClick={selectCenter}>Wybierz środek mapy</button>
      </div>
      {tileError && <p className="map-error" role="status">Nie udało się pobrać części mapy. Sprawdź połączenie z internetem.</p>}
    </div>
  )
}
