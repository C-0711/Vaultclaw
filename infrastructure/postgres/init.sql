--
-- PostgreSQL database dump
--

\restrict q4oBeXKffzJ9g50eqjHf6w3QysrA3ArbmREl9Mnv4lZpwmuncnACPl3etDMnfaW

-- Dumped from database version 16.11 (Debian 16.11-1.pgdg12+1)
-- Dumped by pg_dump version 16.11 (Debian 16.11-1.pgdg12+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;


--
-- Name: EXTENSION "uuid-ossp"; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION "uuid-ossp" IS 'generate universally unique identifiers (UUIDs)';


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


--
-- Name: cleanup_expired_reset_tokens(); Type: FUNCTION; Schema: public; Owner: vault
--

CREATE FUNCTION public.cleanup_expired_reset_tokens() RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM password_reset_tokens
    WHERE expires_at < NOW();

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$;


ALTER FUNCTION public.cleanup_expired_reset_tokens() OWNER TO vault;

--
-- Name: FUNCTION cleanup_expired_reset_tokens(); Type: COMMENT; Schema: public; Owner: vault
--

COMMENT ON FUNCTION public.cleanup_expired_reset_tokens() IS 'Deletes expired password reset tokens';


--
-- Name: find_similar_faces(uuid, public.vector, double precision, integer); Type: FUNCTION; Schema: public; Owner: vault
--

CREATE FUNCTION public.find_similar_faces(p_user_id uuid, p_face_embedding public.vector, p_threshold double precision DEFAULT 0.6, p_limit integer DEFAULT 10) RETURNS TABLE(face_id uuid, item_id uuid, cluster_id uuid, similarity double precision)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    SELECT 
        f.id as face_id,
        f.item_id,
        f.cluster_id,
        1 - (f.embedding <=> p_face_embedding) as similarity
    FROM faces f
    WHERE f.user_id = p_user_id
      AND 1 - (f.embedding <=> p_face_embedding) > p_threshold
    ORDER BY f.embedding <=> p_face_embedding
    LIMIT p_limit;
END;
$$;


ALTER FUNCTION public.find_similar_faces(p_user_id uuid, p_face_embedding public.vector, p_threshold double precision, p_limit integer) OWNER TO vault;

--
-- Name: semantic_search(uuid, public.vector, integer); Type: FUNCTION; Schema: public; Owner: vault
--

CREATE FUNCTION public.semantic_search(p_user_id uuid, p_query_embedding public.vector, p_limit integer DEFAULT 20) RETURNS TABLE(item_id uuid, similarity double precision)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    SELECT 
        e.item_id,
        1 - (e.embedding <=> p_query_embedding) as similarity
    FROM embeddings e
    JOIN vault_items v ON e.item_id = v.id
    WHERE e.user_id = p_user_id
      AND v.deleted_at IS NULL
    ORDER BY e.embedding <=> p_query_embedding
    LIMIT p_limit;
END;
$$;


ALTER FUNCTION public.semantic_search(p_user_id uuid, p_query_embedding public.vector, p_limit integer) OWNER TO vault;

--
-- Name: update_album_count(); Type: FUNCTION; Schema: public; Owner: vault
--

CREATE FUNCTION public.update_album_count() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE albums SET item_count = item_count + 1, updated_at = NOW()
        WHERE id = NEW.album_id;
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE albums SET item_count = GREATEST(0, item_count - 1), updated_at = NOW()
        WHERE id = OLD.album_id;
    END IF;
    RETURN NEW;
END;
$$;


ALTER FUNCTION public.update_album_count() OWNER TO vault;

--
-- Name: update_storage_quota(); Type: FUNCTION; Schema: public; Owner: vault
--

CREATE FUNCTION public.update_storage_quota() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO storage_quotas (user_id, used_bytes, file_count)
        VALUES (NEW.user_id, NEW.file_size, 1)
        ON CONFLICT (user_id) DO UPDATE
        SET used_bytes = storage_quotas.used_bytes + NEW.file_size,
            file_count = storage_quotas.file_count + 1,
            updated_at = NOW();
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE storage_quotas 
        SET used_bytes = GREATEST(0, used_bytes - OLD.file_size),
            file_count = GREATEST(0, file_count - 1),
            updated_at = NOW()
        WHERE user_id = OLD.user_id;
    END IF;
    RETURN NEW;
END;
$$;


ALTER FUNCTION public.update_storage_quota() OWNER TO vault;

--
-- Name: update_updated_at(); Type: FUNCTION; Schema: public; Owner: vault
--

CREATE FUNCTION public.update_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


ALTER FUNCTION public.update_updated_at() OWNER TO vault;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: album_items; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.album_items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    album_id uuid NOT NULL,
    item_id uuid NOT NULL,
    sort_order integer DEFAULT 0,
    added_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.album_items OWNER TO vault;

--
-- Name: albums; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.albums (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    parent_id uuid,
    encrypted_name text NOT NULL,
    encrypted_description text,
    cover_item_id uuid,
    item_count integer DEFAULT 0,
    is_smart_album boolean DEFAULT false,
    smart_criteria jsonb,
    sort_order integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    deleted_at timestamp with time zone
);


ALTER TABLE public.albums OWNER TO vault;

--
-- Name: api_keys; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.api_keys (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    key_hash character varying(64) NOT NULL,
    key_prefix character varying(8) NOT NULL,
    scopes text[] DEFAULT ARRAY['read'::text],
    rate_limit integer DEFAULT 1000,
    last_used_at timestamp with time zone,
    expires_at timestamp with time zone,
    revoked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.api_keys OWNER TO vault;

--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.audit_log (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid,
    action character varying(100) NOT NULL,
    resource_type character varying(50),
    resource_id uuid,
    ip_address inet,
    user_agent text,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.audit_log OWNER TO vault;

--
-- Name: chat_conversations; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.chat_conversations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    type character varying(20) NOT NULL,
    encrypted_name bytea,
    encrypted_avatar bytea,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT chat_conversations_type_check CHECK (((type)::text = ANY ((ARRAY['direct'::character varying, 'group'::character varying, 'channel'::character varying])::text[])))
);


ALTER TABLE public.chat_conversations OWNER TO vault;

--
-- Name: chat_members; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.chat_members (
    conversation_id uuid NOT NULL,
    user_id uuid NOT NULL,
    role character varying(20) DEFAULT 'member'::character varying,
    encrypted_conversation_key bytea NOT NULL,
    joined_at timestamp with time zone DEFAULT now(),
    last_read_at timestamp with time zone,
    muted_until timestamp with time zone
);


ALTER TABLE public.chat_members OWNER TO vault;

--
-- Name: chat_messages; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.chat_messages (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    conversation_id uuid,
    sender_id uuid,
    encrypted_content bytea NOT NULL,
    encrypted_media_ref bytea,
    message_type character varying(20) DEFAULT 'text'::character varying,
    reply_to_id uuid,
    forwarded_from_id uuid,
    created_at timestamp with time zone DEFAULT now(),
    edited_at timestamp with time zone,
    deleted_at timestamp with time zone
);


ALTER TABLE public.chat_messages OWNER TO vault;

--
-- Name: chat_reactions; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.chat_reactions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    message_id uuid,
    user_id uuid,
    emoji character varying(10) NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.chat_reactions OWNER TO vault;

--
-- Name: chat_read_receipts; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.chat_read_receipts (
    conversation_id uuid NOT NULL,
    user_id uuid NOT NULL,
    last_read_message_id uuid,
    read_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.chat_read_receipts OWNER TO vault;

--
-- Name: collaborators; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.collaborators (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    album_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    collaborator_id uuid,
    collaborator_email character varying(255),
    can_view boolean DEFAULT true,
    can_add boolean DEFAULT false,
    can_remove boolean DEFAULT false,
    can_edit boolean DEFAULT false,
    can_share boolean DEFAULT false,
    encrypted_album_key text,
    status character varying(20) DEFAULT 'pending'::character varying,
    invited_at timestamp with time zone DEFAULT now(),
    accepted_at timestamp with time zone,
    CONSTRAINT collaborator_has_identity CHECK (((collaborator_id IS NOT NULL) OR (collaborator_email IS NOT NULL)))
);


ALTER TABLE public.collaborators OWNER TO vault;

--
-- Name: data_exports; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.data_exports (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying,
    file_path text,
    file_size bigint,
    download_count integer DEFAULT 0,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    completed_at timestamp with time zone,
    error_message text
);


ALTER TABLE public.data_exports OWNER TO vault;

--
-- Name: document_metadata; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.document_metadata (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    item_id uuid NOT NULL,
    user_id uuid NOT NULL,
    encrypted_summary text,
    encrypted_entities text,
    category character varying(100),
    encrypted_ocr_text text,
    document_date date,
    expiry_date date,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.document_metadata OWNER TO vault;

--
-- Name: embeddings; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.embeddings (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    item_id uuid NOT NULL,
    user_id uuid NOT NULL,
    embedding_type character varying(50) NOT NULL,
    embedding public.vector(1024),
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.embeddings OWNER TO vault;

--
-- Name: face_clusters; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.face_clusters (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    encrypted_name text,
    relationship character varying(50),
    photo_count integer DEFAULT 0,
    centroid public.vector(512),
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.face_clusters OWNER TO vault;

--
-- Name: faces; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.faces (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    item_id uuid NOT NULL,
    user_id uuid NOT NULL,
    cluster_id uuid,
    bbox_x double precision NOT NULL,
    bbox_y double precision NOT NULL,
    bbox_width double precision NOT NULL,
    bbox_height double precision NOT NULL,
    embedding public.vector(512),
    detection_confidence double precision,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.faces OWNER TO vault;

--
-- Name: import_connections; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.import_connections (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    provider character varying(50) NOT NULL,
    access_token text,
    refresh_token text,
    token_expires_at timestamp with time zone,
    credentials jsonb,
    files_imported integer DEFAULT 0,
    bytes_imported bigint DEFAULT 0,
    last_sync timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    revoked_at timestamp with time zone
);


ALTER TABLE public.import_connections OWNER TO vault;

--
-- Name: import_history; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.import_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    provider character varying(50) NOT NULL,
    source_id text NOT NULL,
    source_hash text,
    vault_item_id uuid,
    imported_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.import_history OWNER TO vault;

--
-- Name: import_jobs; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.import_jobs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    provider character varying(50) NOT NULL,
    paths text[],
    include_shared boolean DEFAULT false,
    preserve_folders boolean DEFAULT true,
    delete_after_import boolean DEFAULT false,
    status character varying(20) DEFAULT 'pending'::character varying,
    total_files integer DEFAULT 0,
    imported_files integer DEFAULT 0,
    failed_files integer DEFAULT 0,
    total_bytes bigint DEFAULT 0,
    imported_bytes bigint DEFAULT 0,
    current_file text,
    errors text[] DEFAULT '{}'::text[],
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.import_jobs OWNER TO vault;

--
-- Name: item_places; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.item_places (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    item_id uuid NOT NULL,
    cluster_id uuid,
    latitude double precision,
    longitude double precision,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.item_places OWNER TO vault;

--
-- Name: item_versions; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.item_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    item_id uuid NOT NULL,
    version_number integer NOT NULL,
    storage_key text NOT NULL,
    file_size bigint NOT NULL,
    encrypted_metadata text,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now(),
    change_type character varying(50),
    change_note text
);


ALTER TABLE public.item_versions OWNER TO vault;

--
-- Name: notifications; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.notifications (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    type character varying(50) NOT NULL,
    title text NOT NULL,
    message text,
    data jsonb,
    read_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.notifications OWNER TO vault;

--
-- Name: oauth_accounts; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.oauth_accounts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    provider character varying(20) NOT NULL,
    provider_user_id character varying(255) NOT NULL,
    email character varying(255),
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.oauth_accounts OWNER TO vault;

--
-- Name: password_reset_tokens; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.password_reset_tokens (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    token character varying(255) NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.password_reset_tokens OWNER TO vault;

--
-- Name: TABLE password_reset_tokens; Type: COMMENT; Schema: public; Owner: vault
--

COMMENT ON TABLE public.password_reset_tokens IS 'Stores password reset tokens with expiration';


--
-- Name: COLUMN password_reset_tokens.token; Type: COMMENT; Schema: public; Owner: vault
--

COMMENT ON COLUMN public.password_reset_tokens.token IS 'Secure random token sent via email';


--
-- Name: COLUMN password_reset_tokens.expires_at; Type: COMMENT; Schema: public; Owner: vault
--

COMMENT ON COLUMN public.password_reset_tokens.expires_at IS 'Token expiration (typically 1 hour)';


--
-- Name: place_clusters; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.place_clusters (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    encrypted_name text,
    place_type character varying(50),
    latitude double precision,
    longitude double precision,
    radius_meters double precision DEFAULT 100,
    city character varying(100),
    country character varying(100),
    photo_count integer DEFAULT 0,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.place_clusters OWNER TO vault;

--
-- Name: processing_queue; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.processing_queue (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    item_id uuid NOT NULL,
    user_id uuid NOT NULL,
    task_type character varying(50) NOT NULL,
    priority integer DEFAULT 5,
    status character varying(50) DEFAULT 'pending'::character varying,
    attempts integer DEFAULT 0,
    max_attempts integer DEFAULT 3,
    error_message text,
    created_at timestamp without time zone DEFAULT now(),
    started_at timestamp without time zone,
    completed_at timestamp without time zone
);


ALTER TABLE public.processing_queue OWNER TO vault;

--
-- Name: rate_limits; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.rate_limits (
    id integer NOT NULL,
    key character varying(200) NOT NULL,
    requests integer DEFAULT 1,
    window_start timestamp with time zone DEFAULT now()
);


ALTER TABLE public.rate_limits OWNER TO vault;

--
-- Name: rate_limits_id_seq; Type: SEQUENCE; Schema: public; Owner: vault
--

CREATE SEQUENCE public.rate_limits_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.rate_limits_id_seq OWNER TO vault;

--
-- Name: rate_limits_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: vault
--

ALTER SEQUENCE public.rate_limits_id_seq OWNED BY public.rate_limits.id;


--
-- Name: share_links; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.share_links (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    share_token character varying(64) NOT NULL,
    item_id uuid,
    album_id uuid,
    encrypted_password text,
    expires_at timestamp with time zone,
    max_downloads integer,
    download_count integer DEFAULT 0,
    allow_download boolean DEFAULT true,
    allow_preview boolean DEFAULT true,
    encrypted_message text,
    created_at timestamp with time zone DEFAULT now(),
    last_accessed_at timestamp with time zone,
    revoked_at timestamp with time zone,
    CONSTRAINT share_has_target CHECK (((item_id IS NOT NULL) OR (album_id IS NOT NULL)))
);


ALTER TABLE public.share_links OWNER TO vault;

--
-- Name: shares; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.shares (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    item_id uuid NOT NULL,
    user_id uuid NOT NULL,
    share_token character varying(100) NOT NULL,
    expires_at timestamp without time zone,
    max_views integer,
    view_count integer DEFAULT 0,
    burn_after_view boolean DEFAULT false,
    created_at timestamp without time zone DEFAULT now(),
    last_accessed timestamp without time zone
);


ALTER TABLE public.shares OWNER TO vault;

--
-- Name: storage_access_log; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.storage_access_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    bucket_name character varying(63),
    object_key text,
    operation character varying(20),
    status_code integer,
    bytes_transferred bigint,
    client_ip inet,
    user_agent text,
    request_id uuid,
    duration_ms integer,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.storage_access_log OWNER TO vault;

--
-- Name: storage_buckets; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.storage_buckets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(63) NOT NULL,
    owner_id uuid,
    quota_bytes bigint DEFAULT 0,
    used_bytes bigint DEFAULT 0,
    object_count integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    deleted_at timestamp with time zone
);


ALTER TABLE public.storage_buckets OWNER TO vault;

--
-- Name: storage_chunks; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.storage_chunks (
    hash character varying(64) NOT NULL,
    size bigint NOT NULL,
    ref_count integer DEFAULT 1,
    compressed boolean DEFAULT false,
    stored_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.storage_chunks OWNER TO vault;

--
-- Name: storage_multipart_parts; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.storage_multipart_parts (
    upload_id uuid NOT NULL,
    part_number integer NOT NULL,
    chunk_hash character varying(64) NOT NULL,
    size bigint NOT NULL,
    etag character varying(64),
    uploaded_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.storage_multipart_parts OWNER TO vault;

--
-- Name: storage_multipart_uploads; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.storage_multipart_uploads (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    bucket_id uuid NOT NULL,
    key text NOT NULL,
    content_type character varying(255),
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    expires_at timestamp with time zone DEFAULT (now() + '24:00:00'::interval)
);


ALTER TABLE public.storage_multipart_uploads OWNER TO vault;

--
-- Name: storage_object_chunks; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.storage_object_chunks (
    object_id uuid NOT NULL,
    chunk_hash character varying(64) NOT NULL,
    chunk_index integer NOT NULL,
    chunk_offset bigint NOT NULL,
    chunk_size bigint NOT NULL
);


ALTER TABLE public.storage_object_chunks OWNER TO vault;

--
-- Name: storage_objects; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.storage_objects (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    bucket_id uuid NOT NULL,
    key text NOT NULL,
    version integer DEFAULT 1,
    size bigint NOT NULL,
    content_type character varying(255),
    etag character varying(64),
    metadata jsonb DEFAULT '{}'::jsonb,
    is_current boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    deleted_at timestamp with time zone
);


ALTER TABLE public.storage_objects OWNER TO vault;

--
-- Name: storage_quotas; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.storage_quotas (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    plan_name character varying(50) DEFAULT 'free'::character varying,
    quota_bytes bigint DEFAULT '5368709120'::bigint,
    used_bytes bigint DEFAULT 0,
    file_count integer DEFAULT 0,
    max_file_size bigint DEFAULT 104857600,
    max_versions_per_file integer DEFAULT 10,
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.storage_quotas OWNER TO vault;

--
-- Name: sync_tokens; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.sync_tokens (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    device_id character varying(100) NOT NULL,
    device_name character varying(255),
    device_public_key text NOT NULL,
    last_sync_version bigint DEFAULT 0,
    last_sync_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.sync_tokens OWNER TO vault;

--
-- Name: user_keys; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.user_keys (
    user_id uuid NOT NULL,
    identity_public_key bytea NOT NULL,
    signed_prekey bytea NOT NULL,
    signed_prekey_signature bytea NOT NULL,
    one_time_prekeys bytea[],
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.user_keys OWNER TO vault;

--
-- Name: user_sessions; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.user_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    token_hash character varying(64) NOT NULL,
    device_name character varying(100),
    device_type character varying(50),
    ip_address inet,
    user_agent text,
    last_active_at timestamp with time zone DEFAULT now(),
    created_at timestamp with time zone DEFAULT now(),
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone
);


ALTER TABLE public.user_sessions OWNER TO vault;

--
-- Name: users; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.users (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    email character varying(255) NOT NULL,
    auth_hash character varying(255),
    salt character varying(255),
    encrypted_master_key text,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    last_login timestamp without time zone,
    storage_quota_bytes bigint DEFAULT '10737418240'::bigint,
    storage_used_bytes bigint DEFAULT 0,
    email_verified_at timestamp with time zone,
    display_name character varying(100),
    language character varying(10) DEFAULT 'de'::character varying,
    timezone character varying(50) DEFAULT 'Europe/Berlin'::character varying,
    deletion_requested_at timestamp with time zone,
    deletion_scheduled_at timestamp with time zone,
    deletion_reason text,
    login_attempts integer DEFAULT 0,
    locked_until timestamp with time zone,
    totp_secret text,
    totp_enabled_at timestamp with time zone,
    recovery_codes text,
    stripe_customer_id character varying(100),
    stripe_subscription_id character varying(100),
    subscription_status character varying(50) DEFAULT 'free'::character varying,
    subscription_ends_at timestamp with time zone,
    email_verified boolean DEFAULT false,
    email_verification_token character varying(255),
    email_verification_expires_at timestamp without time zone,
    deleted_at timestamp without time zone,
    last_login_at timestamp without time zone
);


ALTER TABLE public.users OWNER TO vault;

--
-- Name: COLUMN users.email_verified; Type: COMMENT; Schema: public; Owner: vault
--

COMMENT ON COLUMN public.users.email_verified IS 'Whether user has verified their email address';


--
-- Name: COLUMN users.email_verification_token; Type: COMMENT; Schema: public; Owner: vault
--

COMMENT ON COLUMN public.users.email_verification_token IS 'Token for email verification';


--
-- Name: vault_access_tokens; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.vault_access_tokens (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    space_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    token_hash character varying(255) NOT NULL,
    token_prefix character varying(20) NOT NULL,
    scopes text[] DEFAULT '{read}'::text[] NOT NULL,
    ip_allowlist inet[],
    expires_at timestamp with time zone,
    last_used_at timestamp with time zone,
    last_used_ip inet,
    use_count integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    created_by uuid,
    revoked_at timestamp with time zone
);


ALTER TABLE public.vault_access_tokens OWNER TO vault;

--
-- Name: vault_activity; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.vault_activity (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    space_id uuid,
    actor_id uuid NOT NULL,
    action character varying(50) NOT NULL,
    resource_type character varying(50) NOT NULL,
    resource_id uuid,
    details jsonb DEFAULT '{}'::jsonb,
    ip_address inet,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.vault_activity OWNER TO vault;

--
-- Name: vault_branches; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.vault_branches (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    space_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    parent_branch_id uuid,
    head_snapshot_id uuid,
    protected boolean DEFAULT false,
    protection_rules jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    created_by uuid
);


ALTER TABLE public.vault_branches OWNER TO vault;

--
-- Name: vault_content; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.vault_content (
    id integer NOT NULL,
    storage_key text NOT NULL,
    user_id text NOT NULL,
    encrypted_content bytea NOT NULL,
    original_size integer NOT NULL,
    encrypted_size integer NOT NULL,
    checksum text NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    accessed_at timestamp without time zone
);


ALTER TABLE public.vault_content OWNER TO vault;

--
-- Name: vault_content_id_seq; Type: SEQUENCE; Schema: public; Owner: vault
--

CREATE SEQUENCE public.vault_content_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.vault_content_id_seq OWNER TO vault;

--
-- Name: vault_content_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: vault
--

ALTER SEQUENCE public.vault_content_id_seq OWNED BY public.vault_content.id;


--
-- Name: vault_file_versions; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.vault_file_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    space_id uuid NOT NULL,
    content_hash character varying(64) NOT NULL,
    blob_id uuid,
    size_bytes bigint NOT NULL,
    mime_type character varying(255),
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.vault_file_versions OWNER TO vault;

--
-- Name: vault_items; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.vault_items (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    item_type character varying(50) NOT NULL,
    encrypted_metadata text,
    storage_key character varying(500) NOT NULL,
    file_size bigint NOT NULL,
    mime_type character varying(100),
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    captured_at timestamp without time zone,
    deleted_at timestamp without time zone,
    sync_version bigint DEFAULT 1,
    device_id character varying(100),
    processing_status character varying(50) DEFAULT 'pending'::character varying,
    processed_at timestamp without time zone,
    current_version integer DEFAULT 1,
    source_provider character varying(50),
    source_path text
);


ALTER TABLE public.vault_items OWNER TO vault;

--
-- Name: vault_openclaw_workspaces; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.vault_openclaw_workspaces (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    space_id uuid NOT NULL,
    agents_md text,
    soul_md text,
    tools_md text,
    readme_md text,
    config_yaml text,
    gateway_port integer,
    gateway_status character varying(20) DEFAULT 'stopped'::character varying,
    last_generated_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.vault_openclaw_workspaces OWNER TO vault;

--
-- Name: vault_permission_templates; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.vault_permission_templates (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    can_read boolean DEFAULT true,
    can_write boolean DEFAULT false,
    can_delete boolean DEFAULT false,
    can_publish boolean DEFAULT false,
    can_manage_permissions boolean DEFAULT false,
    can_manage_branches boolean DEFAULT false,
    can_approve_reviews boolean DEFAULT false,
    branch_patterns text[] DEFAULT '{}'::text[],
    path_patterns text[] DEFAULT '{}'::text[],
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.vault_permission_templates OWNER TO vault;

--
-- Name: vault_processing_jobs; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.vault_processing_jobs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    space_id uuid NOT NULL,
    job_type character varying(50) NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying,
    priority integer DEFAULT 5,
    input_data jsonb NOT NULL,
    output_data jsonb,
    error_message text,
    attempts integer DEFAULT 0,
    max_attempts integer DEFAULT 3,
    worker_id character varying(100),
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT vault_processing_jobs_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'processing'::character varying, 'completed'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[])))
);


ALTER TABLE public.vault_processing_jobs OWNER TO vault;

--
-- Name: vault_published_sites; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.vault_published_sites (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    space_id uuid NOT NULL,
    slug character varying(100) NOT NULL,
    custom_domain character varying(255),
    title character varying(255) NOT NULL,
    description text,
    logo_url text,
    favicon_url text,
    branch character varying(100) DEFAULT 'main'::character varying,
    root_path character varying(255) DEFAULT '/'::character varying,
    theme character varying(50) DEFAULT 'default'::character varying,
    primary_color character varying(7) DEFAULT '#2563eb'::character varying,
    custom_css text,
    custom_head text,
    nav_config jsonb DEFAULT '{}'::jsonb,
    visibility character varying(20) DEFAULT 'public'::character varying,
    password_hash character varying(255),
    allowed_emails text[],
    meta_title character varying(255),
    meta_description text,
    og_image_url text,
    status character varying(20) DEFAULT 'draft'::character varying,
    last_published_at timestamp with time zone,
    last_published_snapshot_id uuid,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    created_by uuid,
    CONSTRAINT vault_published_sites_status_check CHECK (((status)::text = ANY ((ARRAY['draft'::character varying, 'published'::character varying, 'archived'::character varying])::text[]))),
    CONSTRAINT vault_published_sites_visibility_check CHECK (((visibility)::text = ANY ((ARRAY['public'::character varying, 'password'::character varying, 'private'::character varying])::text[])))
);


ALTER TABLE public.vault_published_sites OWNER TO vault;

--
-- Name: vault_review_comments; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.vault_review_comments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    review_id uuid NOT NULL,
    author_id uuid NOT NULL,
    body text NOT NULL,
    path text,
    line_number integer,
    reply_to_id uuid,
    resolved boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.vault_review_comments OWNER TO vault;

--
-- Name: vault_reviews; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.vault_reviews (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    space_id uuid NOT NULL,
    number integer NOT NULL,
    title character varying(500) NOT NULL,
    description text,
    source_branch_id uuid NOT NULL,
    target_branch_id uuid NOT NULL,
    status character varying(20) DEFAULT 'open'::character varying,
    created_by uuid NOT NULL,
    reviewers uuid[] DEFAULT '{}'::uuid[],
    labels text[] DEFAULT '{}'::text[],
    merged_at timestamp with time zone,
    merged_by uuid,
    closed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT vault_reviews_status_check CHECK (((status)::text = ANY ((ARRAY['open'::character varying, 'approved'::character varying, 'merged'::character varying, 'closed'::character varying])::text[])))
);


ALTER TABLE public.vault_reviews OWNER TO vault;

--
-- Name: vault_roles; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.vault_roles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    permissions jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.vault_roles OWNER TO vault;

--
-- Name: vault_site_analytics; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.vault_site_analytics (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    site_id uuid NOT NULL,
    date date NOT NULL,
    page_path text NOT NULL,
    views integer DEFAULT 0,
    unique_visitors integer DEFAULT 0,
    avg_time_seconds integer DEFAULT 0,
    referrers jsonb DEFAULT '{}'::jsonb,
    countries jsonb DEFAULT '{}'::jsonb
);


ALTER TABLE public.vault_site_analytics OWNER TO vault;

--
-- Name: vault_snapshots; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.vault_snapshots (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    space_id uuid NOT NULL,
    branch_id uuid NOT NULL,
    parent_snapshot_id uuid,
    message text NOT NULL,
    author_id uuid NOT NULL,
    author_name character varying(255),
    author_email character varying(255),
    tree_hash character varying(64) NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.vault_snapshots OWNER TO vault;

--
-- Name: vault_space_members; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.vault_space_members (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    space_id uuid NOT NULL,
    principal_type character varying(20) NOT NULL,
    principal_id uuid NOT NULL,
    principal_email character varying(255),
    role character varying(50) DEFAULT 'viewer'::character varying NOT NULL,
    template_id uuid,
    custom_permissions jsonb DEFAULT '{}'::jsonb,
    invited_by uuid,
    invited_at timestamp with time zone DEFAULT now(),
    accepted_at timestamp with time zone,
    CONSTRAINT vault_space_members_principal_type_check CHECK (((principal_type)::text = ANY ((ARRAY['user'::character varying, 'group'::character varying, 'team'::character varying])::text[])))
);


ALTER TABLE public.vault_space_members OWNER TO vault;

--
-- Name: vault_spaces; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.vault_spaces (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    slug character varying(255) NOT NULL,
    description text,
    default_branch character varying(100) DEFAULT 'main'::character varying,
    visibility character varying(20) DEFAULT 'private'::character varying,
    settings jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    created_by uuid,
    CONSTRAINT vault_spaces_visibility_check CHECK (((visibility)::text = ANY ((ARRAY['private'::character varying, 'internal'::character varying, 'public'::character varying])::text[])))
);


ALTER TABLE public.vault_spaces OWNER TO vault;

--
-- Name: vault_trees; Type: TABLE; Schema: public; Owner: vault
--

CREATE TABLE public.vault_trees (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    snapshot_id uuid NOT NULL,
    path text NOT NULL,
    type character varying(20) NOT NULL,
    file_version_id uuid,
    mode character varying(10) DEFAULT '644'::character varying,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT vault_trees_type_check CHECK (((type)::text = ANY ((ARRAY['directory'::character varying, 'file'::character varying])::text[])))
);


ALTER TABLE public.vault_trees OWNER TO vault;

--
-- Name: rate_limits id; Type: DEFAULT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.rate_limits ALTER COLUMN id SET DEFAULT nextval('public.rate_limits_id_seq'::regclass);


--
-- Name: vault_content id; Type: DEFAULT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_content ALTER COLUMN id SET DEFAULT nextval('public.vault_content_id_seq'::regclass);


--
-- Name: album_items album_items_album_id_item_id_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.album_items
    ADD CONSTRAINT album_items_album_id_item_id_key UNIQUE (album_id, item_id);


--
-- Name: album_items album_items_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.album_items
    ADD CONSTRAINT album_items_pkey PRIMARY KEY (id);


--
-- Name: albums albums_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.albums
    ADD CONSTRAINT albums_pkey PRIMARY KEY (id);


--
-- Name: api_keys api_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_pkey PRIMARY KEY (id);


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);


--
-- Name: chat_conversations chat_conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.chat_conversations
    ADD CONSTRAINT chat_conversations_pkey PRIMARY KEY (id);


--
-- Name: chat_members chat_members_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.chat_members
    ADD CONSTRAINT chat_members_pkey PRIMARY KEY (conversation_id, user_id);


--
-- Name: chat_messages chat_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.chat_messages
    ADD CONSTRAINT chat_messages_pkey PRIMARY KEY (id);


--
-- Name: chat_reactions chat_reactions_message_id_user_id_emoji_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.chat_reactions
    ADD CONSTRAINT chat_reactions_message_id_user_id_emoji_key UNIQUE (message_id, user_id, emoji);


--
-- Name: chat_reactions chat_reactions_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.chat_reactions
    ADD CONSTRAINT chat_reactions_pkey PRIMARY KEY (id);


--
-- Name: chat_read_receipts chat_read_receipts_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.chat_read_receipts
    ADD CONSTRAINT chat_read_receipts_pkey PRIMARY KEY (conversation_id, user_id);


--
-- Name: collaborators collaborators_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.collaborators
    ADD CONSTRAINT collaborators_pkey PRIMARY KEY (id);


--
-- Name: data_exports data_exports_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.data_exports
    ADD CONSTRAINT data_exports_pkey PRIMARY KEY (id);


--
-- Name: document_metadata document_metadata_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.document_metadata
    ADD CONSTRAINT document_metadata_pkey PRIMARY KEY (id);


--
-- Name: embeddings embeddings_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.embeddings
    ADD CONSTRAINT embeddings_pkey PRIMARY KEY (id);


--
-- Name: face_clusters face_clusters_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.face_clusters
    ADD CONSTRAINT face_clusters_pkey PRIMARY KEY (id);


--
-- Name: faces faces_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.faces
    ADD CONSTRAINT faces_pkey PRIMARY KEY (id);


--
-- Name: import_connections import_connections_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.import_connections
    ADD CONSTRAINT import_connections_pkey PRIMARY KEY (id);


--
-- Name: import_connections import_connections_user_id_provider_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.import_connections
    ADD CONSTRAINT import_connections_user_id_provider_key UNIQUE (user_id, provider);


--
-- Name: import_history import_history_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.import_history
    ADD CONSTRAINT import_history_pkey PRIMARY KEY (id);


--
-- Name: import_history import_history_user_id_provider_source_id_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.import_history
    ADD CONSTRAINT import_history_user_id_provider_source_id_key UNIQUE (user_id, provider, source_id);


--
-- Name: import_jobs import_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.import_jobs
    ADD CONSTRAINT import_jobs_pkey PRIMARY KEY (id);


--
-- Name: item_places item_places_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.item_places
    ADD CONSTRAINT item_places_pkey PRIMARY KEY (id);


--
-- Name: item_versions item_versions_item_id_version_number_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.item_versions
    ADD CONSTRAINT item_versions_item_id_version_number_key UNIQUE (item_id, version_number);


--
-- Name: item_versions item_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.item_versions
    ADD CONSTRAINT item_versions_pkey PRIMARY KEY (id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);


--
-- Name: oauth_accounts oauth_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.oauth_accounts
    ADD CONSTRAINT oauth_accounts_pkey PRIMARY KEY (id);


--
-- Name: oauth_accounts oauth_accounts_provider_provider_user_id_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.oauth_accounts
    ADD CONSTRAINT oauth_accounts_provider_provider_user_id_key UNIQUE (provider, provider_user_id);


--
-- Name: password_reset_tokens password_reset_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_pkey PRIMARY KEY (id);


--
-- Name: password_reset_tokens password_reset_tokens_token_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_token_key UNIQUE (token);


--
-- Name: place_clusters place_clusters_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.place_clusters
    ADD CONSTRAINT place_clusters_pkey PRIMARY KEY (id);


--
-- Name: processing_queue processing_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.processing_queue
    ADD CONSTRAINT processing_queue_pkey PRIMARY KEY (id);


--
-- Name: rate_limits rate_limits_key_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.rate_limits
    ADD CONSTRAINT rate_limits_key_key UNIQUE (key);


--
-- Name: rate_limits rate_limits_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.rate_limits
    ADD CONSTRAINT rate_limits_pkey PRIMARY KEY (id);


--
-- Name: share_links share_links_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.share_links
    ADD CONSTRAINT share_links_pkey PRIMARY KEY (id);


--
-- Name: share_links share_links_share_token_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.share_links
    ADD CONSTRAINT share_links_share_token_key UNIQUE (share_token);


--
-- Name: shares shares_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.shares
    ADD CONSTRAINT shares_pkey PRIMARY KEY (id);


--
-- Name: shares shares_share_token_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.shares
    ADD CONSTRAINT shares_share_token_key UNIQUE (share_token);


--
-- Name: storage_access_log storage_access_log_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.storage_access_log
    ADD CONSTRAINT storage_access_log_pkey PRIMARY KEY (id);


--
-- Name: storage_buckets storage_buckets_name_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.storage_buckets
    ADD CONSTRAINT storage_buckets_name_key UNIQUE (name);


--
-- Name: storage_buckets storage_buckets_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.storage_buckets
    ADD CONSTRAINT storage_buckets_pkey PRIMARY KEY (id);


--
-- Name: storage_chunks storage_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.storage_chunks
    ADD CONSTRAINT storage_chunks_pkey PRIMARY KEY (hash);


--
-- Name: storage_multipart_parts storage_multipart_parts_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.storage_multipart_parts
    ADD CONSTRAINT storage_multipart_parts_pkey PRIMARY KEY (upload_id, part_number);


--
-- Name: storage_multipart_uploads storage_multipart_uploads_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.storage_multipart_uploads
    ADD CONSTRAINT storage_multipart_uploads_pkey PRIMARY KEY (id);


--
-- Name: storage_object_chunks storage_object_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.storage_object_chunks
    ADD CONSTRAINT storage_object_chunks_pkey PRIMARY KEY (object_id, chunk_index);


--
-- Name: storage_objects storage_objects_bucket_id_key_version_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.storage_objects
    ADD CONSTRAINT storage_objects_bucket_id_key_version_key UNIQUE (bucket_id, key, version);


--
-- Name: storage_objects storage_objects_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.storage_objects
    ADD CONSTRAINT storage_objects_pkey PRIMARY KEY (id);


--
-- Name: storage_quotas storage_quotas_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.storage_quotas
    ADD CONSTRAINT storage_quotas_pkey PRIMARY KEY (id);


--
-- Name: storage_quotas storage_quotas_user_id_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.storage_quotas
    ADD CONSTRAINT storage_quotas_user_id_key UNIQUE (user_id);


--
-- Name: sync_tokens sync_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.sync_tokens
    ADD CONSTRAINT sync_tokens_pkey PRIMARY KEY (id);


--
-- Name: sync_tokens sync_tokens_user_id_device_id_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.sync_tokens
    ADD CONSTRAINT sync_tokens_user_id_device_id_key UNIQUE (user_id, device_id);


--
-- Name: password_reset_tokens unique_user_reset; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.password_reset_tokens
    ADD CONSTRAINT unique_user_reset UNIQUE (user_id);


--
-- Name: user_keys user_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.user_keys
    ADD CONSTRAINT user_keys_pkey PRIMARY KEY (user_id);


--
-- Name: user_sessions user_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT user_sessions_pkey PRIMARY KEY (id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: vault_access_tokens vault_access_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_access_tokens
    ADD CONSTRAINT vault_access_tokens_pkey PRIMARY KEY (id);


--
-- Name: vault_activity vault_activity_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_activity
    ADD CONSTRAINT vault_activity_pkey PRIMARY KEY (id);


--
-- Name: vault_branches vault_branches_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_branches
    ADD CONSTRAINT vault_branches_pkey PRIMARY KEY (id);


--
-- Name: vault_branches vault_branches_space_id_name_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_branches
    ADD CONSTRAINT vault_branches_space_id_name_key UNIQUE (space_id, name);


--
-- Name: vault_content vault_content_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_content
    ADD CONSTRAINT vault_content_pkey PRIMARY KEY (id);


--
-- Name: vault_content vault_content_storage_key_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_content
    ADD CONSTRAINT vault_content_storage_key_key UNIQUE (storage_key);


--
-- Name: vault_file_versions vault_file_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_file_versions
    ADD CONSTRAINT vault_file_versions_pkey PRIMARY KEY (id);


--
-- Name: vault_file_versions vault_file_versions_space_id_content_hash_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_file_versions
    ADD CONSTRAINT vault_file_versions_space_id_content_hash_key UNIQUE (space_id, content_hash);


--
-- Name: vault_items vault_items_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_items
    ADD CONSTRAINT vault_items_pkey PRIMARY KEY (id);


--
-- Name: vault_openclaw_workspaces vault_openclaw_workspaces_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_openclaw_workspaces
    ADD CONSTRAINT vault_openclaw_workspaces_pkey PRIMARY KEY (id);


--
-- Name: vault_openclaw_workspaces vault_openclaw_workspaces_space_id_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_openclaw_workspaces
    ADD CONSTRAINT vault_openclaw_workspaces_space_id_key UNIQUE (space_id);


--
-- Name: vault_permission_templates vault_permission_templates_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_permission_templates
    ADD CONSTRAINT vault_permission_templates_pkey PRIMARY KEY (id);


--
-- Name: vault_permission_templates vault_permission_templates_tenant_id_name_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_permission_templates
    ADD CONSTRAINT vault_permission_templates_tenant_id_name_key UNIQUE (tenant_id, name);


--
-- Name: vault_processing_jobs vault_processing_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_processing_jobs
    ADD CONSTRAINT vault_processing_jobs_pkey PRIMARY KEY (id);


--
-- Name: vault_published_sites vault_published_sites_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_published_sites
    ADD CONSTRAINT vault_published_sites_pkey PRIMARY KEY (id);


--
-- Name: vault_published_sites vault_published_sites_slug_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_published_sites
    ADD CONSTRAINT vault_published_sites_slug_key UNIQUE (slug);


--
-- Name: vault_published_sites vault_published_sites_space_id_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_published_sites
    ADD CONSTRAINT vault_published_sites_space_id_key UNIQUE (space_id);


--
-- Name: vault_review_comments vault_review_comments_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_review_comments
    ADD CONSTRAINT vault_review_comments_pkey PRIMARY KEY (id);


--
-- Name: vault_reviews vault_reviews_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_reviews
    ADD CONSTRAINT vault_reviews_pkey PRIMARY KEY (id);


--
-- Name: vault_reviews vault_reviews_space_id_number_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_reviews
    ADD CONSTRAINT vault_reviews_space_id_number_key UNIQUE (space_id, number);


--
-- Name: vault_roles vault_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_roles
    ADD CONSTRAINT vault_roles_pkey PRIMARY KEY (id);


--
-- Name: vault_roles vault_roles_tenant_id_name_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_roles
    ADD CONSTRAINT vault_roles_tenant_id_name_key UNIQUE (tenant_id, name);


--
-- Name: vault_site_analytics vault_site_analytics_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_site_analytics
    ADD CONSTRAINT vault_site_analytics_pkey PRIMARY KEY (id);


--
-- Name: vault_site_analytics vault_site_analytics_site_id_date_page_path_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_site_analytics
    ADD CONSTRAINT vault_site_analytics_site_id_date_page_path_key UNIQUE (site_id, date, page_path);


--
-- Name: vault_snapshots vault_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_snapshots
    ADD CONSTRAINT vault_snapshots_pkey PRIMARY KEY (id);


--
-- Name: vault_space_members vault_space_members_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_space_members
    ADD CONSTRAINT vault_space_members_pkey PRIMARY KEY (id);


--
-- Name: vault_space_members vault_space_members_space_id_principal_type_principal_id_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_space_members
    ADD CONSTRAINT vault_space_members_space_id_principal_type_principal_id_key UNIQUE (space_id, principal_type, principal_id);


--
-- Name: vault_spaces vault_spaces_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_spaces
    ADD CONSTRAINT vault_spaces_pkey PRIMARY KEY (id);


--
-- Name: vault_spaces vault_spaces_tenant_id_slug_key; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_spaces
    ADD CONSTRAINT vault_spaces_tenant_id_slug_key UNIQUE (tenant_id, slug);


--
-- Name: vault_trees vault_trees_pkey; Type: CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_trees
    ADD CONSTRAINT vault_trees_pkey PRIMARY KEY (id);


--
-- Name: idx_access_log_bucket; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_access_log_bucket ON public.storage_access_log USING btree (bucket_name);


--
-- Name: idx_access_log_time; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_access_log_time ON public.storage_access_log USING btree (created_at);


--
-- Name: idx_activity_actor; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_activity_actor ON public.vault_activity USING btree (actor_id);


--
-- Name: idx_activity_created; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_activity_created ON public.vault_activity USING btree (created_at DESC);


--
-- Name: idx_activity_space; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_activity_space ON public.vault_activity USING btree (space_id);


--
-- Name: idx_album_items_album; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_album_items_album ON public.album_items USING btree (album_id);


--
-- Name: idx_album_items_item; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_album_items_item ON public.album_items USING btree (item_id);


--
-- Name: idx_albums_parent; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_albums_parent ON public.albums USING btree (parent_id);


--
-- Name: idx_albums_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_albums_user ON public.albums USING btree (user_id);


--
-- Name: idx_analytics_site_date; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_analytics_site_date ON public.vault_site_analytics USING btree (site_id, date DESC);


--
-- Name: idx_api_keys_hash; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_api_keys_hash ON public.api_keys USING btree (key_hash);


--
-- Name: idx_api_keys_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_api_keys_user ON public.api_keys USING btree (user_id);


--
-- Name: idx_audit_action; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_audit_action ON public.audit_log USING btree (action);


--
-- Name: idx_audit_log_created; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_audit_log_created ON public.audit_log USING btree (created_at);


--
-- Name: idx_audit_log_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_audit_log_user ON public.audit_log USING btree (user_id);


--
-- Name: idx_audit_resource; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_audit_resource ON public.audit_log USING btree (resource_type, resource_id);


--
-- Name: idx_audit_time; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_audit_time ON public.audit_log USING btree (created_at);


--
-- Name: idx_audit_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_audit_user ON public.audit_log USING btree (user_id);


--
-- Name: idx_branches_head; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_branches_head ON public.vault_branches USING btree (head_snapshot_id);


--
-- Name: idx_branches_space; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_branches_space ON public.vault_branches USING btree (space_id);


--
-- Name: idx_chat_members_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_chat_members_user ON public.chat_members USING btree (user_id);


--
-- Name: idx_chat_messages_conversation; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_chat_messages_conversation ON public.chat_messages USING btree (conversation_id, created_at DESC);


--
-- Name: idx_chat_messages_sender; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_chat_messages_sender ON public.chat_messages USING btree (sender_id);


--
-- Name: idx_chat_reactions_message; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_chat_reactions_message ON public.chat_reactions USING btree (message_id);


--
-- Name: idx_collaborators_album; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_collaborators_album ON public.collaborators USING btree (album_id);


--
-- Name: idx_collaborators_email; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_collaborators_email ON public.collaborators USING btree (collaborator_email);


--
-- Name: idx_collaborators_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_collaborators_user ON public.collaborators USING btree (collaborator_id);


--
-- Name: idx_content_key; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_content_key ON public.vault_content USING btree (storage_key);


--
-- Name: idx_content_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_content_user ON public.vault_content USING btree (user_id);


--
-- Name: idx_document_metadata_category; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_document_metadata_category ON public.document_metadata USING btree (category);


--
-- Name: idx_document_metadata_item; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_document_metadata_item ON public.document_metadata USING btree (item_id);


--
-- Name: idx_embeddings_item; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_embeddings_item ON public.embeddings USING btree (item_id);


--
-- Name: idx_embeddings_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_embeddings_user ON public.embeddings USING btree (user_id);


--
-- Name: idx_embeddings_vector; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_embeddings_vector ON public.embeddings USING ivfflat (embedding public.vector_cosine_ops) WITH (lists='100');


--
-- Name: idx_exports_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_exports_user ON public.data_exports USING btree (user_id);


--
-- Name: idx_face_clusters_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_face_clusters_user ON public.face_clusters USING btree (user_id);


--
-- Name: idx_faces_cluster; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_faces_cluster ON public.faces USING btree (cluster_id);


--
-- Name: idx_faces_item; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_faces_item ON public.faces USING btree (item_id);


--
-- Name: idx_faces_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_faces_user ON public.faces USING btree (user_id);


--
-- Name: idx_faces_vector; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_faces_vector ON public.faces USING ivfflat (embedding public.vector_cosine_ops) WITH (lists='100');


--
-- Name: idx_file_versions_hash; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_file_versions_hash ON public.vault_file_versions USING btree (content_hash);


--
-- Name: idx_file_versions_space; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_file_versions_space ON public.vault_file_versions USING btree (space_id);


--
-- Name: idx_import_connections_provider; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_import_connections_provider ON public.import_connections USING btree (provider);


--
-- Name: idx_import_connections_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_import_connections_user ON public.import_connections USING btree (user_id);


--
-- Name: idx_import_history_lookup; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_import_history_lookup ON public.import_history USING btree (user_id, provider, source_id);


--
-- Name: idx_import_jobs_status; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_import_jobs_status ON public.import_jobs USING btree (status);


--
-- Name: idx_import_jobs_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_import_jobs_user ON public.import_jobs USING btree (user_id);


--
-- Name: idx_item_places_cluster; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_item_places_cluster ON public.item_places USING btree (cluster_id);


--
-- Name: idx_item_places_item; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_item_places_item ON public.item_places USING btree (item_id);


--
-- Name: idx_jobs_priority; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_jobs_priority ON public.vault_processing_jobs USING btree (priority DESC, created_at);


--
-- Name: idx_jobs_space; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_jobs_space ON public.vault_processing_jobs USING btree (space_id);


--
-- Name: idx_jobs_status; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_jobs_status ON public.vault_processing_jobs USING btree (status);


--
-- Name: idx_members_space; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_members_space ON public.vault_space_members USING btree (space_id);


--
-- Name: idx_notifications_unread; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_notifications_unread ON public.notifications USING btree (user_id, read_at) WHERE (read_at IS NULL);


--
-- Name: idx_notifications_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_notifications_user ON public.notifications USING btree (user_id);


--
-- Name: idx_oauth_accounts_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_oauth_accounts_user ON public.oauth_accounts USING btree (user_id);


--
-- Name: idx_objects_bucket_key; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_objects_bucket_key ON public.storage_objects USING btree (bucket_id, key) WHERE (is_current = true);


--
-- Name: idx_openclaw_ws_space; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_openclaw_ws_space ON public.vault_openclaw_workspaces USING btree (space_id);


--
-- Name: idx_password_reset_tokens_expires; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_password_reset_tokens_expires ON public.password_reset_tokens USING btree (expires_at);


--
-- Name: idx_password_reset_tokens_token; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_password_reset_tokens_token ON public.password_reset_tokens USING btree (token);


--
-- Name: idx_password_reset_tokens_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_password_reset_tokens_user ON public.password_reset_tokens USING btree (user_id);


--
-- Name: idx_place_clusters_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_place_clusters_user ON public.place_clusters USING btree (user_id);


--
-- Name: idx_processing_queue_item; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_processing_queue_item ON public.processing_queue USING btree (item_id);


--
-- Name: idx_processing_queue_status; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_processing_queue_status ON public.processing_queue USING btree (status, priority);


--
-- Name: idx_quotas_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_quotas_user ON public.storage_quotas USING btree (user_id);


--
-- Name: idx_rate_limits_key; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_rate_limits_key ON public.rate_limits USING btree (key);


--
-- Name: idx_review_comments_path; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_review_comments_path ON public.vault_review_comments USING btree (path);


--
-- Name: idx_review_comments_review; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_review_comments_review ON public.vault_review_comments USING btree (review_id);


--
-- Name: idx_reviews_source; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_reviews_source ON public.vault_reviews USING btree (source_branch_id);


--
-- Name: idx_reviews_space; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_reviews_space ON public.vault_reviews USING btree (space_id);


--
-- Name: idx_reviews_status; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_reviews_status ON public.vault_reviews USING btree (status);


--
-- Name: idx_reviews_target; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_reviews_target ON public.vault_reviews USING btree (target_branch_id);


--
-- Name: idx_sessions_token; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_sessions_token ON public.user_sessions USING btree (token_hash);


--
-- Name: idx_sessions_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_sessions_user ON public.user_sessions USING btree (user_id);


--
-- Name: idx_share_links_album; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_share_links_album ON public.share_links USING btree (album_id);


--
-- Name: idx_share_links_item; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_share_links_item ON public.share_links USING btree (item_id);


--
-- Name: idx_share_links_token; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_share_links_token ON public.share_links USING btree (share_token);


--
-- Name: idx_share_links_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_share_links_user ON public.share_links USING btree (user_id);


--
-- Name: idx_shares_item; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_shares_item ON public.shares USING btree (item_id);


--
-- Name: idx_shares_token; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_shares_token ON public.shares USING btree (share_token);


--
-- Name: idx_sites_slug; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_sites_slug ON public.vault_published_sites USING btree (slug);


--
-- Name: idx_sites_space; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_sites_space ON public.vault_published_sites USING btree (space_id);


--
-- Name: idx_snapshots_branch; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_snapshots_branch ON public.vault_snapshots USING btree (branch_id);


--
-- Name: idx_snapshots_created; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_snapshots_created ON public.vault_snapshots USING btree (created_at DESC);


--
-- Name: idx_snapshots_parent; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_snapshots_parent ON public.vault_snapshots USING btree (parent_snapshot_id);


--
-- Name: idx_snapshots_space; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_snapshots_space ON public.vault_snapshots USING btree (space_id);


--
-- Name: idx_spaces_slug; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_spaces_slug ON public.vault_spaces USING btree (slug);


--
-- Name: idx_spaces_tenant; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_spaces_tenant ON public.vault_spaces USING btree (tenant_id);


--
-- Name: idx_sync_tokens_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_sync_tokens_user ON public.sync_tokens USING btree (user_id);


--
-- Name: idx_tokens_space; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_tokens_space ON public.vault_access_tokens USING btree (space_id);


--
-- Name: idx_trees_path; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_trees_path ON public.vault_trees USING btree (path);


--
-- Name: idx_trees_snapshot; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_trees_snapshot ON public.vault_trees USING btree (snapshot_id);


--
-- Name: idx_trees_snapshot_path; Type: INDEX; Schema: public; Owner: vault
--

CREATE UNIQUE INDEX idx_trees_snapshot_path ON public.vault_trees USING btree (snapshot_id, path);


--
-- Name: idx_users_email; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_users_email ON public.users USING btree (email);


--
-- Name: idx_users_email_verification_token; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_users_email_verification_token ON public.users USING btree (email_verification_token) WHERE (email_verification_token IS NOT NULL);


--
-- Name: idx_vault_items_source; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_vault_items_source ON public.vault_items USING btree (source_provider) WHERE (source_provider IS NOT NULL);


--
-- Name: idx_vault_items_status; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_vault_items_status ON public.vault_items USING btree (processing_status);


--
-- Name: idx_vault_items_sync; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_vault_items_sync ON public.vault_items USING btree (user_id, sync_version);


--
-- Name: idx_vault_items_type; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_vault_items_type ON public.vault_items USING btree (item_type);


--
-- Name: idx_vault_items_user; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_vault_items_user ON public.vault_items USING btree (user_id);


--
-- Name: idx_versions_item; Type: INDEX; Schema: public; Owner: vault
--

CREATE INDEX idx_versions_item ON public.item_versions USING btree (item_id);


--
-- Name: face_clusters face_clusters_updated_at; Type: TRIGGER; Schema: public; Owner: vault
--

CREATE TRIGGER face_clusters_updated_at BEFORE UPDATE ON public.face_clusters FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: place_clusters place_clusters_updated_at; Type: TRIGGER; Schema: public; Owner: vault
--

CREATE TRIGGER place_clusters_updated_at BEFORE UPDATE ON public.place_clusters FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: vault_branches trigger_branches_updated; Type: TRIGGER; Schema: public; Owner: vault
--

CREATE TRIGGER trigger_branches_updated BEFORE UPDATE ON public.vault_branches FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: vault_reviews trigger_reviews_updated; Type: TRIGGER; Schema: public; Owner: vault
--

CREATE TRIGGER trigger_reviews_updated BEFORE UPDATE ON public.vault_reviews FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: vault_published_sites trigger_sites_updated; Type: TRIGGER; Schema: public; Owner: vault
--

CREATE TRIGGER trigger_sites_updated BEFORE UPDATE ON public.vault_published_sites FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: vault_spaces trigger_spaces_updated; Type: TRIGGER; Schema: public; Owner: vault
--

CREATE TRIGGER trigger_spaces_updated BEFORE UPDATE ON public.vault_spaces FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: album_items trigger_update_album_count; Type: TRIGGER; Schema: public; Owner: vault
--

CREATE TRIGGER trigger_update_album_count AFTER INSERT OR DELETE ON public.album_items FOR EACH ROW EXECUTE FUNCTION public.update_album_count();


--
-- Name: vault_items trigger_update_quota; Type: TRIGGER; Schema: public; Owner: vault
--

CREATE TRIGGER trigger_update_quota AFTER INSERT OR DELETE ON public.vault_items FOR EACH ROW EXECUTE FUNCTION public.update_storage_quota();


--
-- Name: users users_updated_at; Type: TRIGGER; Schema: public; Owner: vault
--

CREATE TRIGGER users_updated_at BEFORE UPDATE ON public.users FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: vault_items vault_items_updated_at; Type: TRIGGER; Schema: public; Owner: vault
--

CREATE TRIGGER vault_items_updated_at BEFORE UPDATE ON public.vault_items FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


--
-- Name: album_items album_items_album_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.album_items
    ADD CONSTRAINT album_items_album_id_fkey FOREIGN KEY (album_id) REFERENCES public.albums(id) ON DELETE CASCADE;


--
-- Name: album_items album_items_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.album_items
    ADD CONSTRAINT album_items_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.vault_items(id) ON DELETE CASCADE;


--
-- Name: albums albums_cover_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.albums
    ADD CONSTRAINT albums_cover_item_id_fkey FOREIGN KEY (cover_item_id) REFERENCES public.vault_items(id);


--
-- Name: albums albums_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.albums
    ADD CONSTRAINT albums_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.albums(id);


--
-- Name: albums albums_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.albums
    ADD CONSTRAINT albums_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: api_keys api_keys_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: audit_log audit_log_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: chat_conversations chat_conversations_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.chat_conversations
    ADD CONSTRAINT chat_conversations_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: chat_members chat_members_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.chat_members
    ADD CONSTRAINT chat_members_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.chat_conversations(id) ON DELETE CASCADE;


--
-- Name: chat_members chat_members_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.chat_members
    ADD CONSTRAINT chat_members_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: chat_messages chat_messages_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.chat_messages
    ADD CONSTRAINT chat_messages_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.chat_conversations(id) ON DELETE CASCADE;


--
-- Name: chat_messages chat_messages_reply_to_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.chat_messages
    ADD CONSTRAINT chat_messages_reply_to_id_fkey FOREIGN KEY (reply_to_id) REFERENCES public.chat_messages(id) ON DELETE SET NULL;


--
-- Name: chat_messages chat_messages_sender_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.chat_messages
    ADD CONSTRAINT chat_messages_sender_id_fkey FOREIGN KEY (sender_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: chat_reactions chat_reactions_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.chat_reactions
    ADD CONSTRAINT chat_reactions_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.chat_messages(id) ON DELETE CASCADE;


--
-- Name: chat_reactions chat_reactions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.chat_reactions
    ADD CONSTRAINT chat_reactions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: chat_read_receipts chat_read_receipts_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.chat_read_receipts
    ADD CONSTRAINT chat_read_receipts_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.chat_conversations(id) ON DELETE CASCADE;


--
-- Name: chat_read_receipts chat_read_receipts_last_read_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.chat_read_receipts
    ADD CONSTRAINT chat_read_receipts_last_read_message_id_fkey FOREIGN KEY (last_read_message_id) REFERENCES public.chat_messages(id) ON DELETE SET NULL;


--
-- Name: chat_read_receipts chat_read_receipts_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.chat_read_receipts
    ADD CONSTRAINT chat_read_receipts_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: collaborators collaborators_album_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.collaborators
    ADD CONSTRAINT collaborators_album_id_fkey FOREIGN KEY (album_id) REFERENCES public.albums(id) ON DELETE CASCADE;


--
-- Name: collaborators collaborators_collaborator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.collaborators
    ADD CONSTRAINT collaborators_collaborator_id_fkey FOREIGN KEY (collaborator_id) REFERENCES public.users(id);


--
-- Name: collaborators collaborators_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.collaborators
    ADD CONSTRAINT collaborators_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES public.users(id);


--
-- Name: data_exports data_exports_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.data_exports
    ADD CONSTRAINT data_exports_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: document_metadata document_metadata_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.document_metadata
    ADD CONSTRAINT document_metadata_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.vault_items(id) ON DELETE CASCADE;


--
-- Name: document_metadata document_metadata_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.document_metadata
    ADD CONSTRAINT document_metadata_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: embeddings embeddings_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.embeddings
    ADD CONSTRAINT embeddings_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.vault_items(id) ON DELETE CASCADE;


--
-- Name: embeddings embeddings_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.embeddings
    ADD CONSTRAINT embeddings_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: face_clusters face_clusters_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.face_clusters
    ADD CONSTRAINT face_clusters_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: faces faces_cluster_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.faces
    ADD CONSTRAINT faces_cluster_id_fkey FOREIGN KEY (cluster_id) REFERENCES public.face_clusters(id) ON DELETE SET NULL;


--
-- Name: faces faces_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.faces
    ADD CONSTRAINT faces_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.vault_items(id) ON DELETE CASCADE;


--
-- Name: faces faces_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.faces
    ADD CONSTRAINT faces_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: import_connections import_connections_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.import_connections
    ADD CONSTRAINT import_connections_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: import_history import_history_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.import_history
    ADD CONSTRAINT import_history_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: import_history import_history_vault_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.import_history
    ADD CONSTRAINT import_history_vault_item_id_fkey FOREIGN KEY (vault_item_id) REFERENCES public.vault_items(id);


--
-- Name: import_jobs import_jobs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.import_jobs
    ADD CONSTRAINT import_jobs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: item_places item_places_cluster_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.item_places
    ADD CONSTRAINT item_places_cluster_id_fkey FOREIGN KEY (cluster_id) REFERENCES public.place_clusters(id) ON DELETE SET NULL;


--
-- Name: item_places item_places_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.item_places
    ADD CONSTRAINT item_places_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.vault_items(id) ON DELETE CASCADE;


--
-- Name: item_versions item_versions_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.item_versions
    ADD CONSTRAINT item_versions_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: item_versions item_versions_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.item_versions
    ADD CONSTRAINT item_versions_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.vault_items(id) ON DELETE CASCADE;


--
-- Name: notifications notifications_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: oauth_accounts oauth_accounts_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.oauth_accounts
    ADD CONSTRAINT oauth_accounts_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: password_reset_tokens password_reset_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: place_clusters place_clusters_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.place_clusters
    ADD CONSTRAINT place_clusters_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: processing_queue processing_queue_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.processing_queue
    ADD CONSTRAINT processing_queue_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.vault_items(id) ON DELETE CASCADE;


--
-- Name: processing_queue processing_queue_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.processing_queue
    ADD CONSTRAINT processing_queue_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: share_links share_links_album_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.share_links
    ADD CONSTRAINT share_links_album_id_fkey FOREIGN KEY (album_id) REFERENCES public.albums(id);


--
-- Name: share_links share_links_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.share_links
    ADD CONSTRAINT share_links_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.vault_items(id);


--
-- Name: share_links share_links_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.share_links
    ADD CONSTRAINT share_links_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: shares shares_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.shares
    ADD CONSTRAINT shares_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.vault_items(id) ON DELETE CASCADE;


--
-- Name: shares shares_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.shares
    ADD CONSTRAINT shares_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: storage_multipart_parts storage_multipart_parts_upload_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.storage_multipart_parts
    ADD CONSTRAINT storage_multipart_parts_upload_id_fkey FOREIGN KEY (upload_id) REFERENCES public.storage_multipart_uploads(id) ON DELETE CASCADE;


--
-- Name: storage_multipart_uploads storage_multipart_uploads_bucket_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.storage_multipart_uploads
    ADD CONSTRAINT storage_multipart_uploads_bucket_id_fkey FOREIGN KEY (bucket_id) REFERENCES public.storage_buckets(id);


--
-- Name: storage_object_chunks storage_object_chunks_chunk_hash_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.storage_object_chunks
    ADD CONSTRAINT storage_object_chunks_chunk_hash_fkey FOREIGN KEY (chunk_hash) REFERENCES public.storage_chunks(hash);


--
-- Name: storage_object_chunks storage_object_chunks_object_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.storage_object_chunks
    ADD CONSTRAINT storage_object_chunks_object_id_fkey FOREIGN KEY (object_id) REFERENCES public.storage_objects(id) ON DELETE CASCADE;


--
-- Name: storage_objects storage_objects_bucket_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.storage_objects
    ADD CONSTRAINT storage_objects_bucket_id_fkey FOREIGN KEY (bucket_id) REFERENCES public.storage_buckets(id);


--
-- Name: storage_quotas storage_quotas_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.storage_quotas
    ADD CONSTRAINT storage_quotas_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: sync_tokens sync_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.sync_tokens
    ADD CONSTRAINT sync_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_keys user_keys_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.user_keys
    ADD CONSTRAINT user_keys_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_sessions user_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT user_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: vault_access_tokens vault_access_tokens_space_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_access_tokens
    ADD CONSTRAINT vault_access_tokens_space_id_fkey FOREIGN KEY (space_id) REFERENCES public.vault_spaces(id) ON DELETE CASCADE;


--
-- Name: vault_activity vault_activity_space_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_activity
    ADD CONSTRAINT vault_activity_space_id_fkey FOREIGN KEY (space_id) REFERENCES public.vault_spaces(id) ON DELETE CASCADE;


--
-- Name: vault_branches vault_branches_parent_branch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_branches
    ADD CONSTRAINT vault_branches_parent_branch_id_fkey FOREIGN KEY (parent_branch_id) REFERENCES public.vault_branches(id);


--
-- Name: vault_branches vault_branches_space_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_branches
    ADD CONSTRAINT vault_branches_space_id_fkey FOREIGN KEY (space_id) REFERENCES public.vault_spaces(id) ON DELETE CASCADE;


--
-- Name: vault_file_versions vault_file_versions_space_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_file_versions
    ADD CONSTRAINT vault_file_versions_space_id_fkey FOREIGN KEY (space_id) REFERENCES public.vault_spaces(id) ON DELETE CASCADE;


--
-- Name: vault_items vault_items_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_items
    ADD CONSTRAINT vault_items_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: vault_openclaw_workspaces vault_openclaw_workspaces_space_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_openclaw_workspaces
    ADD CONSTRAINT vault_openclaw_workspaces_space_id_fkey FOREIGN KEY (space_id) REFERENCES public.vault_spaces(id) ON DELETE CASCADE;


--
-- Name: vault_processing_jobs vault_processing_jobs_space_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_processing_jobs
    ADD CONSTRAINT vault_processing_jobs_space_id_fkey FOREIGN KEY (space_id) REFERENCES public.vault_spaces(id) ON DELETE CASCADE;


--
-- Name: vault_published_sites vault_published_sites_last_published_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_published_sites
    ADD CONSTRAINT vault_published_sites_last_published_snapshot_id_fkey FOREIGN KEY (last_published_snapshot_id) REFERENCES public.vault_snapshots(id);


--
-- Name: vault_published_sites vault_published_sites_space_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_published_sites
    ADD CONSTRAINT vault_published_sites_space_id_fkey FOREIGN KEY (space_id) REFERENCES public.vault_spaces(id) ON DELETE CASCADE;


--
-- Name: vault_review_comments vault_review_comments_reply_to_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_review_comments
    ADD CONSTRAINT vault_review_comments_reply_to_id_fkey FOREIGN KEY (reply_to_id) REFERENCES public.vault_review_comments(id);


--
-- Name: vault_review_comments vault_review_comments_review_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_review_comments
    ADD CONSTRAINT vault_review_comments_review_id_fkey FOREIGN KEY (review_id) REFERENCES public.vault_reviews(id) ON DELETE CASCADE;


--
-- Name: vault_reviews vault_reviews_source_branch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_reviews
    ADD CONSTRAINT vault_reviews_source_branch_id_fkey FOREIGN KEY (source_branch_id) REFERENCES public.vault_branches(id);


--
-- Name: vault_reviews vault_reviews_space_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_reviews
    ADD CONSTRAINT vault_reviews_space_id_fkey FOREIGN KEY (space_id) REFERENCES public.vault_spaces(id) ON DELETE CASCADE;


--
-- Name: vault_reviews vault_reviews_target_branch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_reviews
    ADD CONSTRAINT vault_reviews_target_branch_id_fkey FOREIGN KEY (target_branch_id) REFERENCES public.vault_branches(id);


--
-- Name: vault_site_analytics vault_site_analytics_site_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_site_analytics
    ADD CONSTRAINT vault_site_analytics_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.vault_published_sites(id) ON DELETE CASCADE;


--
-- Name: vault_snapshots vault_snapshots_branch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_snapshots
    ADD CONSTRAINT vault_snapshots_branch_id_fkey FOREIGN KEY (branch_id) REFERENCES public.vault_branches(id);


--
-- Name: vault_snapshots vault_snapshots_parent_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_snapshots
    ADD CONSTRAINT vault_snapshots_parent_snapshot_id_fkey FOREIGN KEY (parent_snapshot_id) REFERENCES public.vault_snapshots(id);


--
-- Name: vault_snapshots vault_snapshots_space_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_snapshots
    ADD CONSTRAINT vault_snapshots_space_id_fkey FOREIGN KEY (space_id) REFERENCES public.vault_spaces(id) ON DELETE CASCADE;


--
-- Name: vault_space_members vault_space_members_space_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_space_members
    ADD CONSTRAINT vault_space_members_space_id_fkey FOREIGN KEY (space_id) REFERENCES public.vault_spaces(id) ON DELETE CASCADE;


--
-- Name: vault_space_members vault_space_members_template_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_space_members
    ADD CONSTRAINT vault_space_members_template_id_fkey FOREIGN KEY (template_id) REFERENCES public.vault_permission_templates(id);


--
-- Name: vault_trees vault_trees_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vault
--

ALTER TABLE ONLY public.vault_trees
    ADD CONSTRAINT vault_trees_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES public.vault_snapshots(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict q4oBeXKffzJ9g50eqjHf6w3QysrA3ArbmREl9Mnv4lZpwmuncnACPl3etDMnfaW

