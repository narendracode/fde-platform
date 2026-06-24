-- Create separate database for LangFlow (our app uses POSTGRES_DB from env)
SELECT 'CREATE DATABASE langflow'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'langflow')\gexec
