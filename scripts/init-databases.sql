\set ON_ERROR_STOP on
\getenv warehouse_password FOUNDRY_WAREHOUSE_DB_PASSWORD
\getenv registry_password FOUNDRY_REGISTRY_DB_PASSWORD
CREATE ROLE foundry_warehouse LOGIN PASSWORD :'warehouse_password';
CREATE DATABASE foundry_warehouse OWNER foundry_warehouse;
REVOKE ALL ON DATABASE foundry_warehouse FROM PUBLIC;
CREATE ROLE foundry_registry LOGIN PASSWORD :'registry_password';
CREATE DATABASE foundry_registry OWNER foundry_registry;
REVOKE ALL ON DATABASE foundry_registry FROM PUBLIC;
