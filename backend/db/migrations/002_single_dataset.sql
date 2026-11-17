-- Keep one current services dataset, one grid per cell size, and one feature/assessment
-- result per cell. Experiment runs remain separate because comparing them is a
-- core project function. This migration copies the compatible current results
-- before replacing the old version and provenance tables.

CREATE TEMP TABLE migration_import AS
SELECT id FROM data_imports
WHERE import_status = 'completed'
ORDER BY downloaded_at DESC, id DESC
LIMIT 1;

CREATE TEMP TABLE migration_grids AS
SELECT DISTINCT ON (g.cell_size_m)
    g.grid_version, g.cell_size_m
FROM grid_versions g
JOIN city_boundary b ON b.id = g.boundary_id
JOIN migration_import i ON i.id = b.import_id
ORDER BY g.cell_size_m, g.created_at DESC, g.grid_version DESC;

CREATE TEMP TABLE migration_features AS
SELECT DISTINCT ON (g.grid_version)
    g.grid_version, g.cell_size_m, f.feature_version
FROM migration_grids g
JOIN feature_versions f ON f.grid_version = g.grid_version
JOIN migration_import i ON i.id = f.import_id
ORDER BY g.grid_version, f.computed_at DESC, f.feature_version DESC;

CREATE TEMP TABLE migration_cells AS
SELECT c.id AS old_id,
       'g' || g.cell_size_m || '-c' ||
       lpad(c.col::text, greatest(length(c.col::text), 3), '0') || '-r' ||
       lpad(c.row::text, greatest(length(c.row::text), 3), '0') AS new_id,
       g.cell_size_m, g.grid_version
FROM analysis_cells c
JOIN migration_grids g ON g.grid_version = c.grid_version;

CREATE TABLE city_boundary_v2 (
    id integer PRIMARY KEY CHECK (id = 1),
    name text NOT NULL,
    geometry geometry(MultiPolygon, 2180) NOT NULL CHECK (ST_IsValid(geometry))
);

CREATE TABLE services_v2 (
    id serial PRIMARY KEY,
    name text,
    category text NOT NULL CHECK (category IN ('shop', 'school', 'bus_stop')),
    subcategory text,
    geometry geometry(Point, 2180) NOT NULL,
    inside_city boolean NOT NULL
);

CREATE TABLE land_cover_v2 (
    id serial PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('residential', 'building', 'water', 'forest')),
    geometry geometry(Geometry, 2180) NOT NULL
);

CREATE TABLE analysis_cells_v2 (
    id text PRIMARY KEY,
    cell_size_m integer NOT NULL CHECK (cell_size_m > 0),
    col integer NOT NULL,
    row integer NOT NULL,
    geometry geometry(MultiPolygon, 2180) NOT NULL,
    full_geometry geometry(Polygon, 2180) NOT NULL,
    representative_point geometry(Point, 2180) NOT NULL,
    representative_point_method text NOT NULL
        CHECK (representative_point_method IN ('centroid', 'representative_point')),
    point_land_cover text NOT NULL CHECK (point_land_cover IN ('ok', 'water', 'forest')),
    area_m2 double precision NOT NULL CHECK (area_m2 > 0),
    building_count integer NOT NULL CHECK (building_count >= 0),
    residential_area_m2 double precision NOT NULL CHECK (residential_area_m2 >= 0),
    land_use_status text NOT NULL
        CHECK (land_use_status IN ('potentially_residential', 'no_residential_evidence')),
    analysis_status text NOT NULL CHECK (analysis_status IN ('included', 'excluded_sliver')),
    UNIQUE (cell_size_m, col, row)
);

CREATE TABLE cell_features_v2 (
    cell_id text PRIMARY KEY REFERENCES analysis_cells_v2 (id) ON DELETE CASCADE,
    nearest_shop_m double precision CHECK (nearest_shop_m >= 0),
    nearest_school_m double precision CHECK (nearest_school_m >= 0),
    nearest_bus_stop_m double precision CHECK (nearest_bus_stop_m >= 0),
    shops_500m integer CHECK (shops_500m >= 0),
    shops_1000m integer CHECK (shops_1000m >= 0),
    schools_500m integer CHECK (schools_500m >= 0),
    schools_1000m integer CHECK (schools_1000m >= 0),
    bus_stops_500m integer CHECK (bus_stops_500m >= 0),
    bus_stops_1000m integer CHECK (bus_stops_1000m >= 0),
    shop_status text NOT NULL CHECK (shop_status IN ('ok', 'none_found', 'no_source', 'error')),
    school_status text NOT NULL CHECK (school_status IN ('ok', 'none_found', 'no_source', 'error')),
    bus_stop_status text NOT NULL CHECK (bus_stop_status IN ('ok', 'none_found', 'no_source', 'error')),
    buffer_sufficient boolean NOT NULL
);

CREATE TABLE ml_runs_v2 (
    id text PRIMARY KEY,
    algorithm text NOT NULL CHECK (algorithm IN ('kmeans', 'hdbscan')),
    parameters jsonb NOT NULL,
    cell_size_m integer NOT NULL CHECK (cell_size_m > 0),
    sample_filter text NOT NULL CHECK (sample_filter IN ('all', 'potentially_residential')),
    scaler_name text NOT NULL,
    random_state integer,
    n_samples integer NOT NULL CHECK (n_samples > 0),
    n_clusters integer NOT NULL CHECK (n_clusters >= 0),
    noise_fraction double precision NOT NULL CHECK (noise_fraction BETWEEN 0 AND 1),
    metrics jsonb NOT NULL,
    is_published boolean NOT NULL DEFAULT false
);

CREATE TABLE cluster_assignments_v2 (
    run_id text NOT NULL REFERENCES ml_runs_v2 (id) ON DELETE CASCADE,
    cell_id text NOT NULL REFERENCES analysis_cells_v2 (id) ON DELETE CASCADE,
    cluster_label integer NOT NULL CHECK (cluster_label >= -1),
    PRIMARY KEY (run_id, cell_id)
);

CREATE TABLE cluster_statistics_v2 (
    run_id text NOT NULL REFERENCES ml_runs_v2 (id) ON DELETE CASCADE,
    cluster_label integer NOT NULL CHECK (cluster_label >= -1),
    cell_count integer NOT NULL CHECK (cell_count > 0),
    area_m2 double precision NOT NULL CHECK (area_m2 >= 0),
    sample_share double precision NOT NULL CHECK (sample_share BETWEEN 0 AND 1),
    feature_statistics jsonb NOT NULL,
    PRIMARY KEY (run_id, cluster_label)
);

CREATE TABLE accessibility_results_v2 (
    cell_id text PRIMARY KEY REFERENCES analysis_cells_v2 (id) ON DELETE CASCADE,
    accessibility_score double precision CHECK (accessibility_score BETWEEN 0 AND 1),
    accessibility_class text NOT NULL CHECK (accessibility_class IN ('high', 'medium', 'low', 'no_data')),
    criteria_results jsonb NOT NULL
);

INSERT INTO city_boundary_v2 (id, name, geometry)
SELECT 1, b.name, b.geometry
FROM city_boundary b JOIN migration_import i ON i.id = b.import_id;

INSERT INTO services_v2 (name, category, subcategory, geometry, inside_city)
SELECT s.name, s.category, s.subcategory, s.geometry, s.inside_city
FROM services s JOIN migration_import i ON i.id = s.import_id
ORDER BY s.id;

INSERT INTO land_cover_v2 (kind, geometry)
SELECT l.kind, l.geometry
FROM land_cover l JOIN migration_import i ON i.id = l.import_id
ORDER BY l.id;

INSERT INTO analysis_cells_v2
    (id, cell_size_m, col, row, geometry, full_geometry, representative_point,
     representative_point_method, point_land_cover, area_m2, building_count,
     residential_area_m2, land_use_status, analysis_status)
SELECT m.new_id, m.cell_size_m, c.col, c.row, c.geometry, c.full_geometry,
       c.representative_point, c.representative_point_method, c.point_land_cover,
       c.area_m2, c.building_count, c.residential_area_m2, c.land_use_status,
       c.analysis_status
FROM analysis_cells c JOIN migration_cells m ON m.old_id = c.id;

INSERT INTO cell_features_v2
    (cell_id, nearest_shop_m, nearest_school_m, nearest_bus_stop_m, shops_500m,
     shops_1000m, schools_500m, schools_1000m, bus_stops_500m, bus_stops_1000m,
     shop_status, school_status, bus_stop_status, buffer_sufficient)
SELECT m.new_id, f.nearest_shop_m, f.nearest_school_m, f.nearest_bus_stop_m,
       f.shops_500m, f.shops_1000m, f.schools_500m, f.schools_1000m,
       f.bus_stops_500m, f.bus_stops_1000m, f.shop_status, f.school_status,
       f.bus_stop_status, f.buffer_sufficient
FROM cell_features f
JOIN migration_cells m ON m.old_id = f.cell_id
JOIN migration_features v ON v.grid_version = m.grid_version
                         AND v.feature_version = f.feature_version;

INSERT INTO ml_runs_v2
    (id, algorithm, parameters, cell_size_m, sample_filter, scaler_name,
     random_state, n_samples, n_clusters, noise_fraction, metrics, is_published)
SELECT r.id, r.algorithm, coalesce(r.parameters->'parameters', r.parameters),
       v.cell_size_m, r.sample_filter, r.scaler_name, r.random_state,
       r.n_samples, r.n_clusters, r.noise_fraction, r.metrics,
       r.is_published
FROM ml_runs r JOIN migration_features v
    ON v.feature_version = r.feature_version AND v.grid_version = r.grid_version;

INSERT INTO cluster_assignments_v2 (run_id, cell_id, cluster_label)
SELECT a.run_id, m.new_id, a.cluster_label
FROM cluster_assignments a
JOIN ml_runs_v2 r ON r.id = a.run_id
JOIN migration_cells m ON m.old_id = a.cell_id AND m.cell_size_m = r.cell_size_m;

INSERT INTO cluster_statistics_v2
    (run_id, cluster_label, cell_count, area_m2, sample_share, feature_statistics)
SELECT s.run_id, s.cluster_label, s.cell_count, s.area_m2,
       s.sample_share, s.feature_statistics
FROM cluster_statistics s JOIN ml_runs_v2 r ON r.id = s.run_id;

INSERT INTO accessibility_results_v2
    (cell_id, accessibility_score, accessibility_class, criteria_results)
SELECT DISTINCT ON (m.new_id)
       m.new_id, a.accessibility_score, a.accessibility_class,
       a.criteria_results - 'method' - 'sample_filter' - 'overall_rule'
                          - 'partial_data_note'
FROM accessibility_results a
JOIN assessment_versions v ON v.assessment_version = a.assessment_version
JOIN migration_cells m ON m.old_id = a.cell_id
JOIN migration_features f ON f.grid_version = m.grid_version
                         AND f.feature_version = v.feature_version
WHERE v.variant = 'threshold'
ORDER BY m.new_id, v.created_at DESC, v.assessment_version DESC;

-- All dependent tables are replaced in the same transaction by pipeline.migrate.
DROP TABLE accessibility_results, cluster_statistics, cluster_assignments,
           ml_runs, cell_features, analysis_cells, assessment_versions,
           feature_versions, grid_versions, land_cover, services,
           city_boundary, data_imports;

ALTER TABLE city_boundary_v2 RENAME TO city_boundary;
ALTER TABLE services_v2 RENAME TO services;
ALTER TABLE land_cover_v2 RENAME TO land_cover;
ALTER TABLE analysis_cells_v2 RENAME TO analysis_cells;
ALTER TABLE cell_features_v2 RENAME TO cell_features;
ALTER TABLE ml_runs_v2 RENAME TO ml_runs;
ALTER TABLE cluster_assignments_v2 RENAME TO cluster_assignments;
ALTER TABLE cluster_statistics_v2 RENAME TO cluster_statistics;
ALTER TABLE accessibility_results_v2 RENAME TO accessibility_results;

CREATE INDEX city_boundary_geometry_gix ON city_boundary USING gist (geometry);
CREATE INDEX services_geometry_gix ON services USING gist (geometry);
CREATE INDEX services_category_idx ON services (category);
CREATE INDEX land_cover_geometry_gix ON land_cover USING gist (geometry);
CREATE INDEX land_cover_kind_idx ON land_cover (kind);
CREATE INDEX analysis_cells_geometry_gix ON analysis_cells USING gist (geometry);
CREATE INDEX analysis_cells_point_gix ON analysis_cells USING gist (representative_point);
CREATE INDEX analysis_cells_size_idx ON analysis_cells (cell_size_m);
CREATE INDEX ml_runs_size_idx ON ml_runs (cell_size_m, algorithm);
CREATE INDEX cluster_assignments_cell_idx ON cluster_assignments (cell_id);
