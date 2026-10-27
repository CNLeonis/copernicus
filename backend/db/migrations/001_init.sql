-- All geometries are stored in EPSG:2180 (metres); the API transforms them to EPSG:4326.
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE data_imports (
    id serial PRIMARY KEY,
    source_name text NOT NULL,
    source_url text NOT NULL,
    downloaded_at timestamptz NOT NULL,
    data_version text NOT NULL UNIQUE,
    license_info text NOT NULL,
    record_count integer NOT NULL CHECK (record_count >= 0),
    import_status text NOT NULL CHECK (import_status IN ('completed', 'failed')),
    validation_report jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE city_boundary (
    id serial PRIMARY KEY,
    import_id integer NOT NULL UNIQUE REFERENCES data_imports (id) ON DELETE CASCADE,
    name text NOT NULL,
    source_type text NOT NULL CHECK (source_type IN ('node', 'way', 'relation')),
    source_id bigint NOT NULL,
    geometry geometry(MultiPolygon, 2180) NOT NULL CHECK (ST_IsValid(geometry))
);
CREATE INDEX city_boundary_geometry_gix ON city_boundary USING gist (geometry);

CREATE TABLE services (
    id serial PRIMARY KEY,
    import_id integer NOT NULL REFERENCES data_imports (id) ON DELETE CASCADE,
    source_type text NOT NULL CHECK (source_type IN ('node', 'way', 'relation')),
    source_id bigint NOT NULL,
    name text,
    category text NOT NULL CHECK (category IN ('shop', 'school', 'bus_stop')),
    subcategory text,
    geometry geometry(Point, 2180) NOT NULL,
    source_tags jsonb NOT NULL DEFAULT '{}'::jsonb,
    inside_city boolean NOT NULL,
    UNIQUE (import_id, category, source_type, source_id)
);
CREATE INDEX services_geometry_gix ON services USING gist (geometry);
CREATE INDEX services_import_category_idx ON services (import_id, category);

-- Context layers for the representative point check and the "potentially residential" mode.
CREATE TABLE land_cover (
    id serial PRIMARY KEY,
    import_id integer NOT NULL REFERENCES data_imports (id) ON DELETE CASCADE,
    source_type text NOT NULL CHECK (source_type IN ('node', 'way', 'relation')),
    source_id bigint NOT NULL,
    kind text NOT NULL CHECK (kind IN ('residential', 'building', 'water', 'forest')),
    geometry geometry(Geometry, 2180) NOT NULL,
    UNIQUE (import_id, kind, source_type, source_id)
);
CREATE INDEX land_cover_geometry_gix ON land_cover USING gist (geometry);
CREATE INDEX land_cover_import_kind_idx ON land_cover (import_id, kind);

CREATE TABLE grid_versions (
    grid_version text PRIMARY KEY,
    boundary_id integer NOT NULL REFERENCES city_boundary (id) ON DELETE CASCADE,
    cell_size_m integer NOT NULL CHECK (cell_size_m > 0),
    origin_x double precision NOT NULL,
    origin_y double precision NOT NULL,
    crs text NOT NULL,
    parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE analysis_cells (
    id text PRIMARY KEY,
    grid_version text NOT NULL REFERENCES grid_versions (grid_version) ON DELETE CASCADE,
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
    UNIQUE (grid_version, col, row)
);
CREATE INDEX analysis_cells_geometry_gix ON analysis_cells USING gist (geometry);
CREATE INDEX analysis_cells_point_gix ON analysis_cells USING gist (representative_point);

CREATE TABLE feature_versions (
    feature_version text PRIMARY KEY,
    grid_version text NOT NULL REFERENCES grid_versions (grid_version) ON DELETE CASCADE,
    import_id integer NOT NULL REFERENCES data_imports (id) ON DELETE CASCADE,
    distance_method text NOT NULL CHECK (distance_method IN ('euclidean', 'network')),
    parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
    computed_at timestamptz NOT NULL DEFAULT now()
);

-- NULL distance means "not computed / not found"; it is never replaced by 0.
CREATE TABLE cell_features (
    cell_id text NOT NULL REFERENCES analysis_cells (id) ON DELETE CASCADE,
    feature_version text NOT NULL REFERENCES feature_versions (feature_version) ON DELETE CASCADE,
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
    buffer_sufficient boolean NOT NULL,
    computed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (cell_id, feature_version)
);
CREATE INDEX cell_features_version_idx ON cell_features (feature_version);

CREATE TABLE ml_runs (
    id text PRIMARY KEY,
    algorithm text NOT NULL CHECK (algorithm IN ('kmeans', 'hdbscan')),
    experiment text NOT NULL,
    parameters jsonb NOT NULL,
    feature_version text NOT NULL REFERENCES feature_versions (feature_version) ON DELETE CASCADE,
    grid_version text NOT NULL REFERENCES grid_versions (grid_version) ON DELETE CASCADE,
    sample_filter text NOT NULL CHECK (sample_filter IN ('all', 'potentially_residential')),
    feature_set text NOT NULL,
    feature_names text[] NOT NULL,
    transform text NOT NULL,
    scaler_name text NOT NULL,
    random_state integer,
    n_samples integer NOT NULL CHECK (n_samples > 0),
    n_clusters integer NOT NULL CHECK (n_clusters >= 0),
    noise_fraction double precision NOT NULL CHECK (noise_fraction BETWEEN 0 AND 1),
    metrics jsonb NOT NULL,
    library_versions jsonb NOT NULL,
    is_published boolean NOT NULL DEFAULT false,
    artifact_path text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ml_runs_grid_idx ON ml_runs (grid_version, algorithm);

-- -1 is HDBSCAN noise and is stored as-is.
CREATE TABLE cluster_assignments (
    run_id text NOT NULL REFERENCES ml_runs (id) ON DELETE CASCADE,
    cell_id text NOT NULL REFERENCES analysis_cells (id) ON DELETE CASCADE,
    cluster_label integer NOT NULL CHECK (cluster_label >= -1),
    PRIMARY KEY (run_id, cell_id)
);

CREATE TABLE cluster_statistics (
    run_id text NOT NULL REFERENCES ml_runs (id) ON DELETE CASCADE,
    cluster_label integer NOT NULL CHECK (cluster_label >= -1),
    cell_count integer NOT NULL CHECK (cell_count > 0),
    area_m2 double precision NOT NULL CHECK (area_m2 >= 0),
    sample_share double precision NOT NULL CHECK (sample_share BETWEEN 0 AND 1),
    feature_statistics jsonb NOT NULL,
    PRIMARY KEY (run_id, cluster_label)
);

CREATE TABLE assessment_versions (
    assessment_version text PRIMARY KEY,
    feature_version text NOT NULL REFERENCES feature_versions (feature_version) ON DELETE CASCADE,
    variant text NOT NULL CHECK (variant IN ('relative', 'threshold')),
    parameters jsonb NOT NULL,
    description text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE accessibility_results (
    cell_id text NOT NULL REFERENCES analysis_cells (id) ON DELETE CASCADE,
    assessment_version text NOT NULL
        REFERENCES assessment_versions (assessment_version) ON DELETE CASCADE,
    accessibility_score double precision CHECK (accessibility_score BETWEEN 0 AND 1),
    accessibility_class text NOT NULL CHECK (accessibility_class IN ('high', 'medium', 'low', 'no_data')),
    criteria_results jsonb NOT NULL,
    PRIMARY KEY (cell_id, assessment_version)
);
