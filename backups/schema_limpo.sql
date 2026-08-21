--
-- PostgreSQL database dump
--

\restrict dJXcMxHn5FpQjDSNUlwaLLYUiQdsFreKc5aGKa19vUOcxvUifxDwz1AMokryrKE

-- Dumped from database version 15.18
-- Dumped by pg_dump version 15.18

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
-- Name: evolution; Type: SCHEMA; Schema: -; Owner: admin
--

CREATE SCHEMA evolution;


ALTER SCHEMA evolution OWNER TO admin;

--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: DeviceMessage; Type: TYPE; Schema: evolution; Owner: admin
--

CREATE TYPE evolution."DeviceMessage" AS ENUM (
    'ios',
    'android',
    'web',
    'unknown',
    'desktop'
);


ALTER TYPE evolution."DeviceMessage" OWNER TO admin;

--
-- Name: DifyBotType; Type: TYPE; Schema: evolution; Owner: admin
--

CREATE TYPE evolution."DifyBotType" AS ENUM (
    'chatBot',
    'textGenerator',
    'agent',
    'workflow'
);


ALTER TYPE evolution."DifyBotType" OWNER TO admin;

--
-- Name: InstanceConnectionStatus; Type: TYPE; Schema: evolution; Owner: admin
--

CREATE TYPE evolution."InstanceConnectionStatus" AS ENUM (
    'open',
    'close',
    'connecting'
);


ALTER TYPE evolution."InstanceConnectionStatus" OWNER TO admin;

--
-- Name: OpenaiBotType; Type: TYPE; Schema: evolution; Owner: admin
--

CREATE TYPE evolution."OpenaiBotType" AS ENUM (
    'assistant',
    'chatCompletion'
);


ALTER TYPE evolution."OpenaiBotType" OWNER TO admin;

--
-- Name: SessionStatus; Type: TYPE; Schema: evolution; Owner: admin
--

CREATE TYPE evolution."SessionStatus" AS ENUM (
    'opened',
    'closed',
    'paused'
);


ALTER TYPE evolution."SessionStatus" OWNER TO admin;

--
-- Name: TriggerOperator; Type: TYPE; Schema: evolution; Owner: admin
--

CREATE TYPE evolution."TriggerOperator" AS ENUM (
    'contains',
    'equals',
    'startsWith',
    'endsWith',
    'regex'
);


ALTER TYPE evolution."TriggerOperator" OWNER TO admin;

--
-- Name: TriggerType; Type: TYPE; Schema: evolution; Owner: admin
--

CREATE TYPE evolution."TriggerType" AS ENUM (
    'all',
    'keyword',
    'none',
    'advanced'
);


ALTER TYPE evolution."TriggerType" OWNER TO admin;

--
-- Name: update_timestamp_func(); Type: FUNCTION; Schema: public; Owner: admin
--

CREATE FUNCTION public.update_timestamp_func() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.atualizado_em = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;


ALTER FUNCTION public.update_timestamp_func() OWNER TO admin;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: Chat; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Chat" (
    id text NOT NULL,
    "remoteJid" character varying(100) NOT NULL,
    labels jsonb,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone,
    "instanceId" text NOT NULL,
    name character varying(100),
    "unreadMessages" integer DEFAULT 0 NOT NULL
);


ALTER TABLE evolution."Chat" OWNER TO admin;

--
-- Name: Chatwoot; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Chatwoot" (
    id text NOT NULL,
    enabled boolean DEFAULT true,
    "accountId" character varying(100),
    token character varying(100),
    url character varying(500),
    "nameInbox" character varying(100),
    "signMsg" boolean DEFAULT false,
    "signDelimiter" character varying(100),
    number character varying(100),
    "reopenConversation" boolean DEFAULT false,
    "conversationPending" boolean DEFAULT false,
    "mergeBrazilContacts" boolean DEFAULT false,
    "importContacts" boolean DEFAULT false,
    "importMessages" boolean DEFAULT false,
    "daysLimitImportMessages" integer,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "instanceId" text NOT NULL,
    logo character varying(500),
    organization character varying(100),
    "ignoreJids" jsonb
);


ALTER TABLE evolution."Chatwoot" OWNER TO admin;

--
-- Name: Contact; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Contact" (
    id text NOT NULL,
    "remoteJid" character varying(100) NOT NULL,
    "pushName" character varying(100),
    "profilePicUrl" character varying(500),
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone,
    "instanceId" text NOT NULL
);


ALTER TABLE evolution."Contact" OWNER TO admin;

--
-- Name: Dify; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Dify" (
    id text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    "botType" evolution."DifyBotType" NOT NULL,
    "apiUrl" character varying(255),
    "apiKey" character varying(255),
    expire integer DEFAULT 0,
    "keywordFinish" character varying(100),
    "delayMessage" integer,
    "unknownMessage" character varying(100),
    "listeningFromMe" boolean DEFAULT false,
    "stopBotFromMe" boolean DEFAULT false,
    "keepOpen" boolean DEFAULT false,
    "debounceTime" integer,
    "ignoreJids" jsonb,
    "triggerType" evolution."TriggerType",
    "triggerOperator" evolution."TriggerOperator",
    "triggerValue" text,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "instanceId" text NOT NULL,
    description character varying(255),
    "splitMessages" boolean DEFAULT false,
    "timePerChar" integer DEFAULT 50
);


ALTER TABLE evolution."Dify" OWNER TO admin;

--
-- Name: DifySetting; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."DifySetting" (
    id text NOT NULL,
    expire integer DEFAULT 0,
    "keywordFinish" character varying(100),
    "delayMessage" integer,
    "unknownMessage" character varying(100),
    "listeningFromMe" boolean DEFAULT false,
    "stopBotFromMe" boolean DEFAULT false,
    "keepOpen" boolean DEFAULT false,
    "debounceTime" integer,
    "ignoreJids" jsonb,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "difyIdFallback" character varying(100),
    "instanceId" text NOT NULL,
    "splitMessages" boolean DEFAULT false,
    "timePerChar" integer DEFAULT 50
);


ALTER TABLE evolution."DifySetting" OWNER TO admin;

--
-- Name: Evoai; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Evoai" (
    id text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    description character varying(255),
    "agentUrl" character varying(255),
    "apiKey" character varying(255),
    expire integer DEFAULT 0,
    "keywordFinish" character varying(100),
    "delayMessage" integer,
    "unknownMessage" character varying(100),
    "listeningFromMe" boolean DEFAULT false,
    "stopBotFromMe" boolean DEFAULT false,
    "keepOpen" boolean DEFAULT false,
    "debounceTime" integer,
    "ignoreJids" jsonb,
    "splitMessages" boolean DEFAULT false,
    "timePerChar" integer DEFAULT 50,
    "triggerType" evolution."TriggerType",
    "triggerOperator" evolution."TriggerOperator",
    "triggerValue" text,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "instanceId" text NOT NULL
);


ALTER TABLE evolution."Evoai" OWNER TO admin;

--
-- Name: EvoaiSetting; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."EvoaiSetting" (
    id text NOT NULL,
    expire integer DEFAULT 0,
    "keywordFinish" character varying(100),
    "delayMessage" integer,
    "unknownMessage" character varying(100),
    "listeningFromMe" boolean DEFAULT false,
    "stopBotFromMe" boolean DEFAULT false,
    "keepOpen" boolean DEFAULT false,
    "debounceTime" integer,
    "ignoreJids" jsonb,
    "splitMessages" boolean DEFAULT false,
    "timePerChar" integer DEFAULT 50,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "evoaiIdFallback" character varying(100),
    "instanceId" text NOT NULL
);


ALTER TABLE evolution."EvoaiSetting" OWNER TO admin;

--
-- Name: EvolutionBot; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."EvolutionBot" (
    id text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    description character varying(255),
    "apiUrl" character varying(255),
    "apiKey" character varying(255),
    expire integer DEFAULT 0,
    "keywordFinish" character varying(100),
    "delayMessage" integer,
    "unknownMessage" character varying(100),
    "listeningFromMe" boolean DEFAULT false,
    "stopBotFromMe" boolean DEFAULT false,
    "keepOpen" boolean DEFAULT false,
    "debounceTime" integer,
    "ignoreJids" jsonb,
    "triggerType" evolution."TriggerType",
    "triggerOperator" evolution."TriggerOperator",
    "triggerValue" text,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "instanceId" text NOT NULL,
    "splitMessages" boolean DEFAULT false,
    "timePerChar" integer DEFAULT 50
);


ALTER TABLE evolution."EvolutionBot" OWNER TO admin;

--
-- Name: EvolutionBotSetting; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."EvolutionBotSetting" (
    id text NOT NULL,
    expire integer DEFAULT 0,
    "keywordFinish" character varying(100),
    "delayMessage" integer,
    "unknownMessage" character varying(100),
    "listeningFromMe" boolean DEFAULT false,
    "stopBotFromMe" boolean DEFAULT false,
    "keepOpen" boolean DEFAULT false,
    "debounceTime" integer,
    "ignoreJids" jsonb,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "botIdFallback" character varying(100),
    "instanceId" text NOT NULL,
    "splitMessages" boolean DEFAULT false,
    "timePerChar" integer DEFAULT 50
);


ALTER TABLE evolution."EvolutionBotSetting" OWNER TO admin;

--
-- Name: Flowise; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Flowise" (
    id text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    description character varying(255),
    "apiUrl" character varying(255),
    "apiKey" character varying(255),
    expire integer DEFAULT 0,
    "keywordFinish" character varying(100),
    "delayMessage" integer,
    "unknownMessage" character varying(100),
    "listeningFromMe" boolean DEFAULT false,
    "stopBotFromMe" boolean DEFAULT false,
    "keepOpen" boolean DEFAULT false,
    "debounceTime" integer,
    "ignoreJids" jsonb,
    "triggerType" evolution."TriggerType",
    "triggerOperator" evolution."TriggerOperator",
    "triggerValue" text,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "instanceId" text NOT NULL,
    "splitMessages" boolean DEFAULT false,
    "timePerChar" integer DEFAULT 50
);


ALTER TABLE evolution."Flowise" OWNER TO admin;

--
-- Name: FlowiseSetting; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."FlowiseSetting" (
    id text NOT NULL,
    expire integer DEFAULT 0,
    "keywordFinish" character varying(100),
    "delayMessage" integer,
    "unknownMessage" character varying(100),
    "listeningFromMe" boolean DEFAULT false,
    "stopBotFromMe" boolean DEFAULT false,
    "keepOpen" boolean DEFAULT false,
    "debounceTime" integer,
    "ignoreJids" jsonb,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "flowiseIdFallback" character varying(100),
    "instanceId" text NOT NULL,
    "splitMessages" boolean DEFAULT false,
    "timePerChar" integer DEFAULT 50
);


ALTER TABLE evolution."FlowiseSetting" OWNER TO admin;

--
-- Name: Instance; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Instance" (
    id text NOT NULL,
    name character varying(255) NOT NULL,
    "connectionStatus" evolution."InstanceConnectionStatus" DEFAULT 'open'::evolution."InstanceConnectionStatus" NOT NULL,
    "ownerJid" character varying(100),
    "profilePicUrl" character varying(500),
    integration character varying(100),
    number character varying(100),
    token character varying(255),
    "clientName" character varying(100),
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone,
    "profileName" character varying(100),
    "businessId" character varying(100),
    "disconnectionAt" timestamp without time zone,
    "disconnectionObject" jsonb,
    "disconnectionReasonCode" integer
);


ALTER TABLE evolution."Instance" OWNER TO admin;

--
-- Name: IntegrationSession; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."IntegrationSession" (
    id text NOT NULL,
    "sessionId" character varying(255) NOT NULL,
    "remoteJid" character varying(100) NOT NULL,
    "pushName" text,
    status evolution."SessionStatus" NOT NULL,
    "awaitUser" boolean DEFAULT false NOT NULL,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "instanceId" text NOT NULL,
    parameters jsonb,
    context jsonb,
    "botId" text,
    type character varying(100)
);


ALTER TABLE evolution."IntegrationSession" OWNER TO admin;

--
-- Name: IsOnWhatsapp; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."IsOnWhatsapp" (
    id text NOT NULL,
    "remoteJid" character varying(100) NOT NULL,
    "jidOptions" text NOT NULL,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updatedAt" timestamp without time zone NOT NULL,
    lid character varying(100)
);


ALTER TABLE evolution."IsOnWhatsapp" OWNER TO admin;

--
-- Name: Kafka; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Kafka" (
    id text NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    events jsonb NOT NULL,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "instanceId" text NOT NULL
);


ALTER TABLE evolution."Kafka" OWNER TO admin;

--
-- Name: Label; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Label" (
    id text NOT NULL,
    "labelId" character varying(100),
    name character varying(100) NOT NULL,
    color character varying(100) NOT NULL,
    "predefinedId" character varying(100),
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "instanceId" text NOT NULL
);


ALTER TABLE evolution."Label" OWNER TO admin;

--
-- Name: Media; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Media" (
    id text NOT NULL,
    "fileName" character varying(500) NOT NULL,
    type character varying(100) NOT NULL,
    mimetype character varying(100) NOT NULL,
    "createdAt" date DEFAULT CURRENT_TIMESTAMP,
    "messageId" text NOT NULL,
    "instanceId" text NOT NULL
);


ALTER TABLE evolution."Media" OWNER TO admin;

--
-- Name: Message; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Message" (
    id text NOT NULL,
    key jsonb NOT NULL,
    "pushName" character varying(100),
    participant character varying(100),
    "messageType" character varying(100) NOT NULL,
    message jsonb NOT NULL,
    "contextInfo" jsonb,
    source evolution."DeviceMessage" NOT NULL,
    "messageTimestamp" integer NOT NULL,
    "chatwootMessageId" integer,
    "chatwootInboxId" integer,
    "chatwootConversationId" integer,
    "chatwootContactInboxSourceId" character varying(100),
    "chatwootIsRead" boolean,
    "instanceId" text NOT NULL,
    "webhookUrl" character varying(500),
    "sessionId" text,
    status character varying(30)
);


ALTER TABLE evolution."Message" OWNER TO admin;

--
-- Name: MessageUpdate; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."MessageUpdate" (
    id text NOT NULL,
    "keyId" character varying(100) NOT NULL,
    "remoteJid" character varying(100) NOT NULL,
    "fromMe" boolean NOT NULL,
    participant character varying(100),
    "pollUpdates" jsonb,
    status character varying(30) NOT NULL,
    "messageId" text NOT NULL,
    "instanceId" text NOT NULL
);


ALTER TABLE evolution."MessageUpdate" OWNER TO admin;

--
-- Name: N8n; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."N8n" (
    id text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    description character varying(255),
    "webhookUrl" character varying(255),
    "basicAuthUser" character varying(255),
    "basicAuthPass" character varying(255),
    expire integer DEFAULT 0,
    "keywordFinish" character varying(100),
    "delayMessage" integer,
    "unknownMessage" character varying(100),
    "listeningFromMe" boolean DEFAULT false,
    "stopBotFromMe" boolean DEFAULT false,
    "keepOpen" boolean DEFAULT false,
    "debounceTime" integer,
    "ignoreJids" jsonb,
    "splitMessages" boolean DEFAULT false,
    "timePerChar" integer DEFAULT 50,
    "triggerType" evolution."TriggerType",
    "triggerOperator" evolution."TriggerOperator",
    "triggerValue" text,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "instanceId" text NOT NULL
);


ALTER TABLE evolution."N8n" OWNER TO admin;

--
-- Name: N8nSetting; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."N8nSetting" (
    id text NOT NULL,
    expire integer DEFAULT 0,
    "keywordFinish" character varying(100),
    "delayMessage" integer,
    "unknownMessage" character varying(100),
    "listeningFromMe" boolean DEFAULT false,
    "stopBotFromMe" boolean DEFAULT false,
    "keepOpen" boolean DEFAULT false,
    "debounceTime" integer,
    "ignoreJids" jsonb,
    "splitMessages" boolean DEFAULT false,
    "timePerChar" integer DEFAULT 50,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "n8nIdFallback" character varying(100),
    "instanceId" text NOT NULL
);


ALTER TABLE evolution."N8nSetting" OWNER TO admin;

--
-- Name: Nats; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Nats" (
    id text NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    events jsonb NOT NULL,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "instanceId" text NOT NULL
);


ALTER TABLE evolution."Nats" OWNER TO admin;

--
-- Name: OpenaiBot; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."OpenaiBot" (
    id text NOT NULL,
    "assistantId" character varying(255),
    model character varying(100),
    "systemMessages" jsonb,
    "assistantMessages" jsonb,
    "userMessages" jsonb,
    "maxTokens" integer,
    expire integer DEFAULT 0,
    "keywordFinish" character varying(100),
    "delayMessage" integer,
    "unknownMessage" character varying(100),
    "listeningFromMe" boolean DEFAULT false,
    "stopBotFromMe" boolean DEFAULT false,
    "keepOpen" boolean DEFAULT false,
    "debounceTime" integer,
    "ignoreJids" jsonb,
    "triggerType" evolution."TriggerType",
    "triggerOperator" evolution."TriggerOperator",
    "triggerValue" text,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "openaiCredsId" text NOT NULL,
    "instanceId" text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    "botType" evolution."OpenaiBotType" NOT NULL,
    description character varying(255),
    "functionUrl" character varying(500),
    "splitMessages" boolean DEFAULT false,
    "timePerChar" integer DEFAULT 50
);


ALTER TABLE evolution."OpenaiBot" OWNER TO admin;

--
-- Name: OpenaiCreds; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."OpenaiCreds" (
    id text NOT NULL,
    "apiKey" character varying(255),
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "instanceId" text NOT NULL,
    name character varying(255)
);


ALTER TABLE evolution."OpenaiCreds" OWNER TO admin;

--
-- Name: OpenaiSetting; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."OpenaiSetting" (
    id text NOT NULL,
    expire integer DEFAULT 0,
    "keywordFinish" character varying(100),
    "delayMessage" integer,
    "unknownMessage" character varying(100),
    "listeningFromMe" boolean DEFAULT false,
    "stopBotFromMe" boolean DEFAULT false,
    "keepOpen" boolean DEFAULT false,
    "debounceTime" integer,
    "ignoreJids" jsonb,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "openaiCredsId" text NOT NULL,
    "openaiIdFallback" character varying(100),
    "instanceId" text NOT NULL,
    "speechToText" boolean DEFAULT false,
    "splitMessages" boolean DEFAULT false,
    "timePerChar" integer DEFAULT 50
);


ALTER TABLE evolution."OpenaiSetting" OWNER TO admin;

--
-- Name: Proxy; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Proxy" (
    id text NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    host character varying(100) NOT NULL,
    port character varying(100) NOT NULL,
    protocol character varying(100) NOT NULL,
    username character varying(100) NOT NULL,
    password character varying(100) NOT NULL,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "instanceId" text NOT NULL
);


ALTER TABLE evolution."Proxy" OWNER TO admin;

--
-- Name: Pusher; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Pusher" (
    id text NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    "appId" character varying(100) NOT NULL,
    key character varying(100) NOT NULL,
    secret character varying(100) NOT NULL,
    cluster character varying(100) NOT NULL,
    "useTLS" boolean DEFAULT false NOT NULL,
    events jsonb NOT NULL,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "instanceId" text NOT NULL
);


ALTER TABLE evolution."Pusher" OWNER TO admin;

--
-- Name: Rabbitmq; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Rabbitmq" (
    id text NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    events jsonb NOT NULL,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "instanceId" text NOT NULL
);


ALTER TABLE evolution."Rabbitmq" OWNER TO admin;

--
-- Name: Session; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Session" (
    id text NOT NULL,
    "sessionId" text NOT NULL,
    creds text,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


ALTER TABLE evolution."Session" OWNER TO admin;

--
-- Name: Setting; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Setting" (
    id text NOT NULL,
    "rejectCall" boolean DEFAULT false NOT NULL,
    "msgCall" character varying(100),
    "groupsIgnore" boolean DEFAULT false NOT NULL,
    "alwaysOnline" boolean DEFAULT false NOT NULL,
    "readMessages" boolean DEFAULT false NOT NULL,
    "readStatus" boolean DEFAULT false NOT NULL,
    "syncFullHistory" boolean DEFAULT false NOT NULL,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "instanceId" text NOT NULL,
    "wavoipToken" character varying(100)
);


ALTER TABLE evolution."Setting" OWNER TO admin;

--
-- Name: Sqs; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Sqs" (
    id text NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    events jsonb NOT NULL,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "instanceId" text NOT NULL
);


ALTER TABLE evolution."Sqs" OWNER TO admin;

--
-- Name: Template; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Template" (
    id text NOT NULL,
    "templateId" character varying(255) NOT NULL,
    name character varying(255) NOT NULL,
    template jsonb NOT NULL,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "instanceId" text NOT NULL,
    "webhookUrl" character varying(500)
);


ALTER TABLE evolution."Template" OWNER TO admin;

--
-- Name: Typebot; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Typebot" (
    id text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    url character varying(500) NOT NULL,
    typebot character varying(100) NOT NULL,
    expire integer DEFAULT 0,
    "keywordFinish" character varying(100),
    "delayMessage" integer,
    "unknownMessage" character varying(100),
    "listeningFromMe" boolean DEFAULT false,
    "stopBotFromMe" boolean DEFAULT false,
    "keepOpen" boolean DEFAULT false,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone,
    "triggerType" evolution."TriggerType",
    "triggerOperator" evolution."TriggerOperator",
    "triggerValue" text,
    "instanceId" text NOT NULL,
    "debounceTime" integer,
    "ignoreJids" jsonb,
    description character varying(255),
    "splitMessages" boolean DEFAULT false,
    "timePerChar" integer DEFAULT 50
);


ALTER TABLE evolution."Typebot" OWNER TO admin;

--
-- Name: TypebotSetting; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."TypebotSetting" (
    id text NOT NULL,
    expire integer DEFAULT 0,
    "keywordFinish" character varying(100),
    "delayMessage" integer,
    "unknownMessage" character varying(100),
    "listeningFromMe" boolean DEFAULT false,
    "stopBotFromMe" boolean DEFAULT false,
    "keepOpen" boolean DEFAULT false,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "instanceId" text NOT NULL,
    "debounceTime" integer,
    "typebotIdFallback" character varying(100),
    "ignoreJids" jsonb,
    "splitMessages" boolean DEFAULT false,
    "timePerChar" integer DEFAULT 50
);


ALTER TABLE evolution."TypebotSetting" OWNER TO admin;

--
-- Name: Webhook; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Webhook" (
    id text NOT NULL,
    url character varying(500) NOT NULL,
    enabled boolean DEFAULT true,
    events jsonb,
    "webhookByEvents" boolean DEFAULT false,
    "webhookBase64" boolean DEFAULT false,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "instanceId" text NOT NULL,
    headers jsonb
);


ALTER TABLE evolution."Webhook" OWNER TO admin;

--
-- Name: Websocket; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution."Websocket" (
    id text NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    events jsonb NOT NULL,
    "createdAt" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" timestamp without time zone NOT NULL,
    "instanceId" text NOT NULL
);


ALTER TABLE evolution."Websocket" OWNER TO admin;

--
-- Name: _prisma_migrations; Type: TABLE; Schema: evolution; Owner: admin
--

CREATE TABLE evolution._prisma_migrations (
    id character varying(36) NOT NULL,
    checksum character varying(64) NOT NULL,
    finished_at timestamp with time zone,
    migration_name character varying(255) NOT NULL,
    logs text,
    rolled_back_at timestamp with time zone,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    applied_steps_count integer DEFAULT 0 NOT NULL
);


ALTER TABLE evolution._prisma_migrations OWNER TO admin;

--
-- Name: assinaturas; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.assinaturas (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    motorista_id uuid NOT NULL,
    gateway_id character varying(100),
    plano character varying(50) NOT NULL,
    data_vencimento date NOT NULL,
    status character varying(20) NOT NULL,
    criado_em timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.assinaturas OWNER TO admin;

--
-- Name: caixas_provisao; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.caixas_provisao (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    motorista_id uuid NOT NULL,
    nome_caixa character varying(50) NOT NULL,
    saldo_atual numeric(14,4) DEFAULT 0.00,
    ultima_atualizacao timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.caixas_provisao OWNER TO admin;

--
-- Name: despesas_fixas_mensais; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.despesas_fixas_mensais (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    motorista_id uuid NOT NULL,
    nome character varying(50) NOT NULL,
    valor_mensal numeric(14,4) NOT NULL,
    dias_trabalho_previstos integer NOT NULL,
    valor_pro_rata_diario numeric(14,4) GENERATED ALWAYS AS ((valor_mensal / (NULLIF(dias_trabalho_previstos, 0))::numeric)) STORED,
    dia_vencimento integer NOT NULL,
    ativo boolean DEFAULT true,
    CONSTRAINT despesas_fixas_mensais_dias_trabalho_previstos_check CHECK ((dias_trabalho_previstos > 0))
);


ALTER TABLE public.despesas_fixas_mensais OWNER TO admin;

--
-- Name: dlq_eventos; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.dlq_eventos (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    motorista_id uuid,
    payload_original jsonb NOT NULL,
    motivo_falha text NOT NULL,
    tentativas integer DEFAULT 1,
    status character varying(20) DEFAULT 'pendente'::character varying,
    criado_em timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.dlq_eventos OWNER TO admin;

--
-- Name: fechamento_diario; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.fechamento_diario (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    motorista_id uuid NOT NULL,
    turno_id uuid NOT NULL,
    faturamento_bruto numeric(14,4) NOT NULL,
    custo_variavel_direto numeric(14,4) NOT NULL,
    custo_fixo_rateado numeric(14,4) NOT NULL,
    lucro_liquido_real numeric(14,4) NOT NULL,
    km_rodados numeric(10,2) NOT NULL,
    clima_predominante character varying(30),
    data_fechamento date DEFAULT CURRENT_DATE
);


ALTER TABLE public.fechamento_diario OWNER TO admin;

--
-- Name: historico_manutencao; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.historico_manutencao (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    veiculo_id uuid NOT NULL,
    regra_id uuid,
    transacao_id uuid,
    km_execucao numeric(10,2) NOT NULL,
    data_execucao timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.historico_manutencao OWNER TO admin;

--
-- Name: lgpd_logs; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.lgpd_logs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    motorista_id uuid NOT NULL,
    acao_realizada character varying(50) NOT NULL,
    ip_origem character varying(50),
    data_evento timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.lgpd_logs OWNER TO admin;

--
-- Name: motoristas; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.motoristas (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    telefone character varying(20) NOT NULL,
    nome character varying(100) NOT NULL,
    status_assinatura character varying(20) DEFAULT 'TRIAL'::character varying,
    criado_em timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    atualizado_em timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    ativo boolean DEFAULT true,
    meta_mensal_faturamento numeric(10,2) DEFAULT 12000.00,
    dias_uteis_mes integer DEFAULT 26
);


ALTER TABLE public.motoristas OWNER TO admin;

--
-- Name: pausas_turno; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.pausas_turno (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    turno_id uuid NOT NULL,
    inicio_pausa timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    fim_pausa timestamp with time zone,
    motivo character varying(50)
);


ALTER TABLE public.pausas_turno OWNER TO admin;

--
-- Name: regras_manutencao; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.regras_manutencao (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    veiculo_id uuid NOT NULL,
    tipo_servico character varying(100) NOT NULL,
    intervalo_km integer NOT NULL,
    aviso_previo_km integer DEFAULT 500 NOT NULL,
    ativo boolean DEFAULT true
);


ALTER TABLE public.regras_manutencao OWNER TO admin;

--
-- Name: transacoes; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.transacoes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    motorista_id uuid NOT NULL,
    turno_id uuid,
    veiculo_id uuid,
    tipo_movimentacao character varying(20) NOT NULL,
    categoria character varying(50) NOT NULL,
    valor numeric(14,4) NOT NULL,
    estabelecimento character varying(100),
    metodo_pagamento character varying(50),
    contexto_operacional character varying(50),
    comprovante_url character varying(255),
    idempotencia_hash character varying(100),
    estornado boolean DEFAULT false,
    data_transacao timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    descricao text,
    wpp_msg_id character varying(255),
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT transacoes_tipo_movimentacao_check CHECK (((tipo_movimentacao)::text = ANY ((ARRAY['receita'::character varying, 'despesa'::character varying, 'neutro'::character varying])::text[])))
);


ALTER TABLE public.transacoes OWNER TO admin;

--
-- Name: turnos; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.turnos (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    motorista_id uuid NOT NULL,
    veiculo_id uuid NOT NULL,
    km_inicial numeric(10,2) NOT NULL,
    km_final numeric(10,2),
    data_inicio timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    data_fim timestamp with time zone,
    status character varying(20) DEFAULT 'em_andamento'::character varying,
    CONSTRAINT turnos_check CHECK (((km_final IS NULL) OR (km_final >= km_inicial)))
);


ALTER TABLE public.turnos OWNER TO admin;

--
-- Name: veiculos; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.veiculos (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    motorista_id uuid NOT NULL,
    placa character varying(10) NOT NULL,
    modelo character varying(50) NOT NULL,
    tipo_combustivel character varying(30) NOT NULL,
    estoque_financeiro jsonb DEFAULT '{"liquido": {"litros": 0, "custo_total": 0}, "eletricidade": {"kwh": 0, "custo_total": 0}}'::jsonb,
    ativo boolean DEFAULT true,
    criado_em timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    locadora character varying(100) DEFAULT 'Localiza Zarp'::character varying,
    custo_aluguel_semanal numeric(10,2) DEFAULT 1020.85,
    franquia_km_semanal numeric(10,2) DEFAULT 1505.00,
    valor_km_excedente numeric(10,4) DEFAULT 0.75,
    escala_trabalho character varying(100) DEFAULT 'De quarta a segunda (6 dias)'::character varying,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.veiculos OWNER TO admin;

--
-- Name: Chat Chat_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Chat"
    ADD CONSTRAINT "Chat_pkey" PRIMARY KEY (id);


--
-- Name: Chatwoot Chatwoot_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Chatwoot"
    ADD CONSTRAINT "Chatwoot_pkey" PRIMARY KEY (id);


--
-- Name: Contact Contact_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Contact"
    ADD CONSTRAINT "Contact_pkey" PRIMARY KEY (id);


--
-- Name: DifySetting DifySetting_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."DifySetting"
    ADD CONSTRAINT "DifySetting_pkey" PRIMARY KEY (id);


--
-- Name: Dify Dify_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Dify"
    ADD CONSTRAINT "Dify_pkey" PRIMARY KEY (id);


--
-- Name: EvoaiSetting EvoaiSetting_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."EvoaiSetting"
    ADD CONSTRAINT "EvoaiSetting_pkey" PRIMARY KEY (id);


--
-- Name: Evoai Evoai_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Evoai"
    ADD CONSTRAINT "Evoai_pkey" PRIMARY KEY (id);


--
-- Name: EvolutionBotSetting EvolutionBotSetting_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."EvolutionBotSetting"
    ADD CONSTRAINT "EvolutionBotSetting_pkey" PRIMARY KEY (id);


--
-- Name: EvolutionBot EvolutionBot_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."EvolutionBot"
    ADD CONSTRAINT "EvolutionBot_pkey" PRIMARY KEY (id);


--
-- Name: FlowiseSetting FlowiseSetting_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."FlowiseSetting"
    ADD CONSTRAINT "FlowiseSetting_pkey" PRIMARY KEY (id);


--
-- Name: Flowise Flowise_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Flowise"
    ADD CONSTRAINT "Flowise_pkey" PRIMARY KEY (id);


--
-- Name: Instance Instance_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Instance"
    ADD CONSTRAINT "Instance_pkey" PRIMARY KEY (id);


--
-- Name: IntegrationSession IntegrationSession_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."IntegrationSession"
    ADD CONSTRAINT "IntegrationSession_pkey" PRIMARY KEY (id);


--
-- Name: IsOnWhatsapp IsOnWhatsapp_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."IsOnWhatsapp"
    ADD CONSTRAINT "IsOnWhatsapp_pkey" PRIMARY KEY (id);


--
-- Name: Kafka Kafka_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Kafka"
    ADD CONSTRAINT "Kafka_pkey" PRIMARY KEY (id);


--
-- Name: Label Label_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Label"
    ADD CONSTRAINT "Label_pkey" PRIMARY KEY (id);


--
-- Name: Media Media_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Media"
    ADD CONSTRAINT "Media_pkey" PRIMARY KEY (id);


--
-- Name: MessageUpdate MessageUpdate_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."MessageUpdate"
    ADD CONSTRAINT "MessageUpdate_pkey" PRIMARY KEY (id);


--
-- Name: Message Message_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Message"
    ADD CONSTRAINT "Message_pkey" PRIMARY KEY (id);


--
-- Name: N8nSetting N8nSetting_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."N8nSetting"
    ADD CONSTRAINT "N8nSetting_pkey" PRIMARY KEY (id);


--
-- Name: N8n N8n_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."N8n"
    ADD CONSTRAINT "N8n_pkey" PRIMARY KEY (id);


--
-- Name: Nats Nats_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Nats"
    ADD CONSTRAINT "Nats_pkey" PRIMARY KEY (id);


--
-- Name: OpenaiBot OpenaiBot_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."OpenaiBot"
    ADD CONSTRAINT "OpenaiBot_pkey" PRIMARY KEY (id);


--
-- Name: OpenaiCreds OpenaiCreds_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."OpenaiCreds"
    ADD CONSTRAINT "OpenaiCreds_pkey" PRIMARY KEY (id);


--
-- Name: OpenaiSetting OpenaiSetting_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."OpenaiSetting"
    ADD CONSTRAINT "OpenaiSetting_pkey" PRIMARY KEY (id);


--
-- Name: Proxy Proxy_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Proxy"
    ADD CONSTRAINT "Proxy_pkey" PRIMARY KEY (id);


--
-- Name: Pusher Pusher_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Pusher"
    ADD CONSTRAINT "Pusher_pkey" PRIMARY KEY (id);


--
-- Name: Rabbitmq Rabbitmq_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Rabbitmq"
    ADD CONSTRAINT "Rabbitmq_pkey" PRIMARY KEY (id);


--
-- Name: Session Session_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Session"
    ADD CONSTRAINT "Session_pkey" PRIMARY KEY (id);


--
-- Name: Setting Setting_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Setting"
    ADD CONSTRAINT "Setting_pkey" PRIMARY KEY (id);


--
-- Name: Sqs Sqs_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Sqs"
    ADD CONSTRAINT "Sqs_pkey" PRIMARY KEY (id);


--
-- Name: Template Template_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Template"
    ADD CONSTRAINT "Template_pkey" PRIMARY KEY (id);


--
-- Name: TypebotSetting TypebotSetting_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."TypebotSetting"
    ADD CONSTRAINT "TypebotSetting_pkey" PRIMARY KEY (id);


--
-- Name: Typebot Typebot_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Typebot"
    ADD CONSTRAINT "Typebot_pkey" PRIMARY KEY (id);


--
-- Name: Webhook Webhook_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Webhook"
    ADD CONSTRAINT "Webhook_pkey" PRIMARY KEY (id);


--
-- Name: Websocket Websocket_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Websocket"
    ADD CONSTRAINT "Websocket_pkey" PRIMARY KEY (id);


--
-- Name: _prisma_migrations _prisma_migrations_pkey; Type: CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution._prisma_migrations
    ADD CONSTRAINT _prisma_migrations_pkey PRIMARY KEY (id);


--
-- Name: assinaturas assinaturas_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.assinaturas
    ADD CONSTRAINT assinaturas_pkey PRIMARY KEY (id);


--
-- Name: caixas_provisao caixas_provisao_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.caixas_provisao
    ADD CONSTRAINT caixas_provisao_pkey PRIMARY KEY (id);


--
-- Name: despesas_fixas_mensais despesas_fixas_mensais_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.despesas_fixas_mensais
    ADD CONSTRAINT despesas_fixas_mensais_pkey PRIMARY KEY (id);


--
-- Name: dlq_eventos dlq_eventos_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.dlq_eventos
    ADD CONSTRAINT dlq_eventos_pkey PRIMARY KEY (id);


--
-- Name: fechamento_diario fechamento_diario_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.fechamento_diario
    ADD CONSTRAINT fechamento_diario_pkey PRIMARY KEY (id);


--
-- Name: historico_manutencao historico_manutencao_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.historico_manutencao
    ADD CONSTRAINT historico_manutencao_pkey PRIMARY KEY (id);


--
-- Name: lgpd_logs lgpd_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.lgpd_logs
    ADD CONSTRAINT lgpd_logs_pkey PRIMARY KEY (id);


--
-- Name: motoristas motoristas_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.motoristas
    ADD CONSTRAINT motoristas_pkey PRIMARY KEY (id);


--
-- Name: motoristas motoristas_telefone_key; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.motoristas
    ADD CONSTRAINT motoristas_telefone_key UNIQUE (telefone);


--
-- Name: pausas_turno pausas_turno_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.pausas_turno
    ADD CONSTRAINT pausas_turno_pkey PRIMARY KEY (id);


--
-- Name: regras_manutencao regras_manutencao_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.regras_manutencao
    ADD CONSTRAINT regras_manutencao_pkey PRIMARY KEY (id);


--
-- Name: transacoes transacoes_idempotencia_hash_key; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.transacoes
    ADD CONSTRAINT transacoes_idempotencia_hash_key UNIQUE (idempotencia_hash);


--
-- Name: transacoes transacoes_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.transacoes
    ADD CONSTRAINT transacoes_pkey PRIMARY KEY (id);


--
-- Name: turnos turnos_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.turnos
    ADD CONSTRAINT turnos_pkey PRIMARY KEY (id);


--
-- Name: transacoes unique_wpp_msg_id; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.transacoes
    ADD CONSTRAINT unique_wpp_msg_id UNIQUE (wpp_msg_id);


--
-- Name: veiculos veiculos_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.veiculos
    ADD CONSTRAINT veiculos_pkey PRIMARY KEY (id);


--
-- Name: Chat_instanceId_idx; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE INDEX "Chat_instanceId_idx" ON evolution."Chat" USING btree ("instanceId");


--
-- Name: Chat_instanceId_remoteJid_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "Chat_instanceId_remoteJid_key" ON evolution."Chat" USING btree ("instanceId", "remoteJid");


--
-- Name: Chat_remoteJid_idx; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE INDEX "Chat_remoteJid_idx" ON evolution."Chat" USING btree ("remoteJid");


--
-- Name: Chatwoot_instanceId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "Chatwoot_instanceId_key" ON evolution."Chatwoot" USING btree ("instanceId");


--
-- Name: Contact_instanceId_idx; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE INDEX "Contact_instanceId_idx" ON evolution."Contact" USING btree ("instanceId");


--
-- Name: Contact_remoteJid_idx; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE INDEX "Contact_remoteJid_idx" ON evolution."Contact" USING btree ("remoteJid");


--
-- Name: Contact_remoteJid_instanceId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "Contact_remoteJid_instanceId_key" ON evolution."Contact" USING btree ("remoteJid", "instanceId");


--
-- Name: DifySetting_instanceId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "DifySetting_instanceId_key" ON evolution."DifySetting" USING btree ("instanceId");


--
-- Name: EvoaiSetting_instanceId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "EvoaiSetting_instanceId_key" ON evolution."EvoaiSetting" USING btree ("instanceId");


--
-- Name: EvolutionBotSetting_instanceId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "EvolutionBotSetting_instanceId_key" ON evolution."EvolutionBotSetting" USING btree ("instanceId");


--
-- Name: FlowiseSetting_instanceId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "FlowiseSetting_instanceId_key" ON evolution."FlowiseSetting" USING btree ("instanceId");


--
-- Name: Instance_name_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "Instance_name_key" ON evolution."Instance" USING btree (name);


--
-- Name: IsOnWhatsapp_remoteJid_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "IsOnWhatsapp_remoteJid_key" ON evolution."IsOnWhatsapp" USING btree ("remoteJid");


--
-- Name: Kafka_instanceId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "Kafka_instanceId_key" ON evolution."Kafka" USING btree ("instanceId");


--
-- Name: Label_labelId_instanceId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "Label_labelId_instanceId_key" ON evolution."Label" USING btree ("labelId", "instanceId");


--
-- Name: Media_messageId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "Media_messageId_key" ON evolution."Media" USING btree ("messageId");


--
-- Name: MessageUpdate_instanceId_idx; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE INDEX "MessageUpdate_instanceId_idx" ON evolution."MessageUpdate" USING btree ("instanceId");


--
-- Name: MessageUpdate_messageId_idx; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE INDEX "MessageUpdate_messageId_idx" ON evolution."MessageUpdate" USING btree ("messageId");


--
-- Name: Message_instanceId_idx; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE INDEX "Message_instanceId_idx" ON evolution."Message" USING btree ("instanceId");


--
-- Name: N8nSetting_instanceId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "N8nSetting_instanceId_key" ON evolution."N8nSetting" USING btree ("instanceId");


--
-- Name: Nats_instanceId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "Nats_instanceId_key" ON evolution."Nats" USING btree ("instanceId");


--
-- Name: OpenaiCreds_apiKey_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "OpenaiCreds_apiKey_key" ON evolution."OpenaiCreds" USING btree ("apiKey");


--
-- Name: OpenaiCreds_name_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "OpenaiCreds_name_key" ON evolution."OpenaiCreds" USING btree (name);


--
-- Name: OpenaiSetting_instanceId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "OpenaiSetting_instanceId_key" ON evolution."OpenaiSetting" USING btree ("instanceId");


--
-- Name: OpenaiSetting_openaiCredsId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "OpenaiSetting_openaiCredsId_key" ON evolution."OpenaiSetting" USING btree ("openaiCredsId");


--
-- Name: Proxy_instanceId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "Proxy_instanceId_key" ON evolution."Proxy" USING btree ("instanceId");


--
-- Name: Pusher_instanceId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "Pusher_instanceId_key" ON evolution."Pusher" USING btree ("instanceId");


--
-- Name: Rabbitmq_instanceId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "Rabbitmq_instanceId_key" ON evolution."Rabbitmq" USING btree ("instanceId");


--
-- Name: Session_sessionId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "Session_sessionId_key" ON evolution."Session" USING btree ("sessionId");


--
-- Name: Setting_instanceId_idx; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE INDEX "Setting_instanceId_idx" ON evolution."Setting" USING btree ("instanceId");


--
-- Name: Setting_instanceId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "Setting_instanceId_key" ON evolution."Setting" USING btree ("instanceId");


--
-- Name: Sqs_instanceId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "Sqs_instanceId_key" ON evolution."Sqs" USING btree ("instanceId");


--
-- Name: Template_name_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "Template_name_key" ON evolution."Template" USING btree (name);


--
-- Name: Template_templateId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "Template_templateId_key" ON evolution."Template" USING btree ("templateId");


--
-- Name: TypebotSetting_instanceId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "TypebotSetting_instanceId_key" ON evolution."TypebotSetting" USING btree ("instanceId");


--
-- Name: Webhook_instanceId_idx; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE INDEX "Webhook_instanceId_idx" ON evolution."Webhook" USING btree ("instanceId");


--
-- Name: Webhook_instanceId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "Webhook_instanceId_key" ON evolution."Webhook" USING btree ("instanceId");


--
-- Name: Websocket_instanceId_key; Type: INDEX; Schema: evolution; Owner: admin
--

CREATE UNIQUE INDEX "Websocket_instanceId_key" ON evolution."Websocket" USING btree ("instanceId");


--
-- Name: idx_assinaturas_motorista; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_assinaturas_motorista ON public.assinaturas USING btree (motorista_id);


--
-- Name: idx_caixas_provisao_motorista; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_caixas_provisao_motorista ON public.caixas_provisao USING btree (motorista_id);


--
-- Name: idx_despesas_fixas_motorista; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_despesas_fixas_motorista ON public.despesas_fixas_mensais USING btree (motorista_id);


--
-- Name: idx_dlq_motorista; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_dlq_motorista ON public.dlq_eventos USING btree (motorista_id);


--
-- Name: idx_dlq_payload_gin; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_dlq_payload_gin ON public.dlq_eventos USING gin (payload_original);


--
-- Name: idx_fechamento_motorista; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_fechamento_motorista ON public.fechamento_diario USING btree (motorista_id);


--
-- Name: idx_fechamento_turno; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_fechamento_turno ON public.fechamento_diario USING btree (turno_id);


--
-- Name: idx_historico_veiculo; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_historico_veiculo ON public.historico_manutencao USING btree (veiculo_id);


--
-- Name: idx_lgpd_motorista; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_lgpd_motorista ON public.lgpd_logs USING btree (motorista_id);


--
-- Name: idx_pausas_turno; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_pausas_turno ON public.pausas_turno USING btree (turno_id);


--
-- Name: idx_regras_veiculo; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_regras_veiculo ON public.regras_manutencao USING btree (veiculo_id);


--
-- Name: idx_transacoes_created; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_transacoes_created ON public.transacoes USING btree (created_at);


--
-- Name: idx_transacoes_data; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_transacoes_data ON public.transacoes USING btree (data_transacao);


--
-- Name: idx_transacoes_motorista; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_transacoes_motorista ON public.transacoes USING btree (motorista_id);


--
-- Name: idx_transacoes_turno; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_transacoes_turno ON public.transacoes USING btree (turno_id);


--
-- Name: idx_transacoes_veiculo; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_transacoes_veiculo ON public.transacoes USING btree (veiculo_id);


--
-- Name: idx_turnos_motorista; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_turnos_motorista ON public.turnos USING btree (motorista_id);


--
-- Name: idx_turnos_veiculo; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_turnos_veiculo ON public.turnos USING btree (veiculo_id);


--
-- Name: idx_veiculos_estoque_gin; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_veiculos_estoque_gin ON public.veiculos USING gin (estoque_financeiro);


--
-- Name: idx_veiculos_motorista; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_veiculos_motorista ON public.veiculos USING btree (motorista_id);


--
-- Name: caixas_provisao trig_caixas_atualizacao; Type: TRIGGER; Schema: public; Owner: admin
--

CREATE TRIGGER trig_caixas_atualizacao BEFORE UPDATE ON public.caixas_provisao FOR EACH ROW EXECUTE FUNCTION public.update_timestamp_func();


--
-- Name: motoristas trig_motoristas_atualizado_em; Type: TRIGGER; Schema: public; Owner: admin
--

CREATE TRIGGER trig_motoristas_atualizado_em BEFORE UPDATE ON public.motoristas FOR EACH ROW EXECUTE FUNCTION public.update_timestamp_func();


--
-- Name: Chat Chat_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Chat"
    ADD CONSTRAINT "Chat_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Chatwoot Chatwoot_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Chatwoot"
    ADD CONSTRAINT "Chatwoot_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Contact Contact_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Contact"
    ADD CONSTRAINT "Contact_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: DifySetting DifySetting_difyIdFallback_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."DifySetting"
    ADD CONSTRAINT "DifySetting_difyIdFallback_fkey" FOREIGN KEY ("difyIdFallback") REFERENCES evolution."Dify"(id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: DifySetting DifySetting_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."DifySetting"
    ADD CONSTRAINT "DifySetting_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Dify Dify_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Dify"
    ADD CONSTRAINT "Dify_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: EvoaiSetting EvoaiSetting_evoaiIdFallback_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."EvoaiSetting"
    ADD CONSTRAINT "EvoaiSetting_evoaiIdFallback_fkey" FOREIGN KEY ("evoaiIdFallback") REFERENCES evolution."Evoai"(id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: EvoaiSetting EvoaiSetting_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."EvoaiSetting"
    ADD CONSTRAINT "EvoaiSetting_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Evoai Evoai_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Evoai"
    ADD CONSTRAINT "Evoai_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: EvolutionBotSetting EvolutionBotSetting_botIdFallback_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."EvolutionBotSetting"
    ADD CONSTRAINT "EvolutionBotSetting_botIdFallback_fkey" FOREIGN KEY ("botIdFallback") REFERENCES evolution."EvolutionBot"(id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: EvolutionBotSetting EvolutionBotSetting_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."EvolutionBotSetting"
    ADD CONSTRAINT "EvolutionBotSetting_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: EvolutionBot EvolutionBot_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."EvolutionBot"
    ADD CONSTRAINT "EvolutionBot_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: FlowiseSetting FlowiseSetting_flowiseIdFallback_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."FlowiseSetting"
    ADD CONSTRAINT "FlowiseSetting_flowiseIdFallback_fkey" FOREIGN KEY ("flowiseIdFallback") REFERENCES evolution."Flowise"(id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: FlowiseSetting FlowiseSetting_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."FlowiseSetting"
    ADD CONSTRAINT "FlowiseSetting_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Flowise Flowise_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Flowise"
    ADD CONSTRAINT "Flowise_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: IntegrationSession IntegrationSession_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."IntegrationSession"
    ADD CONSTRAINT "IntegrationSession_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Kafka Kafka_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Kafka"
    ADD CONSTRAINT "Kafka_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Label Label_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Label"
    ADD CONSTRAINT "Label_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Media Media_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Media"
    ADD CONSTRAINT "Media_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Media Media_messageId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Media"
    ADD CONSTRAINT "Media_messageId_fkey" FOREIGN KEY ("messageId") REFERENCES evolution."Message"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: MessageUpdate MessageUpdate_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."MessageUpdate"
    ADD CONSTRAINT "MessageUpdate_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: MessageUpdate MessageUpdate_messageId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."MessageUpdate"
    ADD CONSTRAINT "MessageUpdate_messageId_fkey" FOREIGN KEY ("messageId") REFERENCES evolution."Message"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Message Message_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Message"
    ADD CONSTRAINT "Message_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Message Message_sessionId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Message"
    ADD CONSTRAINT "Message_sessionId_fkey" FOREIGN KEY ("sessionId") REFERENCES evolution."IntegrationSession"(id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: N8nSetting N8nSetting_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."N8nSetting"
    ADD CONSTRAINT "N8nSetting_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: N8nSetting N8nSetting_n8nIdFallback_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."N8nSetting"
    ADD CONSTRAINT "N8nSetting_n8nIdFallback_fkey" FOREIGN KEY ("n8nIdFallback") REFERENCES evolution."N8n"(id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: N8n N8n_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."N8n"
    ADD CONSTRAINT "N8n_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Nats Nats_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Nats"
    ADD CONSTRAINT "Nats_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: OpenaiBot OpenaiBot_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."OpenaiBot"
    ADD CONSTRAINT "OpenaiBot_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: OpenaiBot OpenaiBot_openaiCredsId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."OpenaiBot"
    ADD CONSTRAINT "OpenaiBot_openaiCredsId_fkey" FOREIGN KEY ("openaiCredsId") REFERENCES evolution."OpenaiCreds"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: OpenaiCreds OpenaiCreds_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."OpenaiCreds"
    ADD CONSTRAINT "OpenaiCreds_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: OpenaiSetting OpenaiSetting_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."OpenaiSetting"
    ADD CONSTRAINT "OpenaiSetting_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: OpenaiSetting OpenaiSetting_openaiCredsId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."OpenaiSetting"
    ADD CONSTRAINT "OpenaiSetting_openaiCredsId_fkey" FOREIGN KEY ("openaiCredsId") REFERENCES evolution."OpenaiCreds"(id) ON UPDATE CASCADE ON DELETE RESTRICT;


--
-- Name: OpenaiSetting OpenaiSetting_openaiIdFallback_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."OpenaiSetting"
    ADD CONSTRAINT "OpenaiSetting_openaiIdFallback_fkey" FOREIGN KEY ("openaiIdFallback") REFERENCES evolution."OpenaiBot"(id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: Proxy Proxy_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Proxy"
    ADD CONSTRAINT "Proxy_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Pusher Pusher_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Pusher"
    ADD CONSTRAINT "Pusher_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Rabbitmq Rabbitmq_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Rabbitmq"
    ADD CONSTRAINT "Rabbitmq_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Session Session_sessionId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Session"
    ADD CONSTRAINT "Session_sessionId_fkey" FOREIGN KEY ("sessionId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Setting Setting_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Setting"
    ADD CONSTRAINT "Setting_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Sqs Sqs_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Sqs"
    ADD CONSTRAINT "Sqs_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Template Template_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Template"
    ADD CONSTRAINT "Template_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: TypebotSetting TypebotSetting_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."TypebotSetting"
    ADD CONSTRAINT "TypebotSetting_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: TypebotSetting TypebotSetting_typebotIdFallback_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."TypebotSetting"
    ADD CONSTRAINT "TypebotSetting_typebotIdFallback_fkey" FOREIGN KEY ("typebotIdFallback") REFERENCES evolution."Typebot"(id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: Typebot Typebot_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Typebot"
    ADD CONSTRAINT "Typebot_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Webhook Webhook_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Webhook"
    ADD CONSTRAINT "Webhook_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: Websocket Websocket_instanceId_fkey; Type: FK CONSTRAINT; Schema: evolution; Owner: admin
--

ALTER TABLE ONLY evolution."Websocket"
    ADD CONSTRAINT "Websocket_instanceId_fkey" FOREIGN KEY ("instanceId") REFERENCES evolution."Instance"(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: assinaturas assinaturas_motorista_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.assinaturas
    ADD CONSTRAINT assinaturas_motorista_id_fkey FOREIGN KEY (motorista_id) REFERENCES public.motoristas(id) ON DELETE RESTRICT;


--
-- Name: caixas_provisao caixas_provisao_motorista_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.caixas_provisao
    ADD CONSTRAINT caixas_provisao_motorista_id_fkey FOREIGN KEY (motorista_id) REFERENCES public.motoristas(id) ON DELETE RESTRICT;


--
-- Name: despesas_fixas_mensais despesas_fixas_mensais_motorista_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.despesas_fixas_mensais
    ADD CONSTRAINT despesas_fixas_mensais_motorista_id_fkey FOREIGN KEY (motorista_id) REFERENCES public.motoristas(id) ON DELETE RESTRICT;


--
-- Name: fechamento_diario fechamento_diario_motorista_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.fechamento_diario
    ADD CONSTRAINT fechamento_diario_motorista_id_fkey FOREIGN KEY (motorista_id) REFERENCES public.motoristas(id) ON DELETE RESTRICT;


--
-- Name: fechamento_diario fechamento_diario_turno_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.fechamento_diario
    ADD CONSTRAINT fechamento_diario_turno_id_fkey FOREIGN KEY (turno_id) REFERENCES public.turnos(id) ON DELETE RESTRICT;


--
-- Name: historico_manutencao historico_manutencao_regra_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.historico_manutencao
    ADD CONSTRAINT historico_manutencao_regra_id_fkey FOREIGN KEY (regra_id) REFERENCES public.regras_manutencao(id) ON DELETE SET NULL;


--
-- Name: historico_manutencao historico_manutencao_transacao_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.historico_manutencao
    ADD CONSTRAINT historico_manutencao_transacao_id_fkey FOREIGN KEY (transacao_id) REFERENCES public.transacoes(id) ON DELETE RESTRICT;


--
-- Name: historico_manutencao historico_manutencao_veiculo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.historico_manutencao
    ADD CONSTRAINT historico_manutencao_veiculo_id_fkey FOREIGN KEY (veiculo_id) REFERENCES public.veiculos(id) ON DELETE RESTRICT;


--
-- Name: pausas_turno pausas_turno_turno_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.pausas_turno
    ADD CONSTRAINT pausas_turno_turno_id_fkey FOREIGN KEY (turno_id) REFERENCES public.turnos(id) ON DELETE CASCADE;


--
-- Name: regras_manutencao regras_manutencao_veiculo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.regras_manutencao
    ADD CONSTRAINT regras_manutencao_veiculo_id_fkey FOREIGN KEY (veiculo_id) REFERENCES public.veiculos(id) ON DELETE CASCADE;


--
-- Name: transacoes transacoes_motorista_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.transacoes
    ADD CONSTRAINT transacoes_motorista_id_fkey FOREIGN KEY (motorista_id) REFERENCES public.motoristas(id) ON DELETE RESTRICT;


--
-- Name: transacoes transacoes_turno_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.transacoes
    ADD CONSTRAINT transacoes_turno_id_fkey FOREIGN KEY (turno_id) REFERENCES public.turnos(id) ON DELETE RESTRICT;


--
-- Name: transacoes transacoes_veiculo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.transacoes
    ADD CONSTRAINT transacoes_veiculo_id_fkey FOREIGN KEY (veiculo_id) REFERENCES public.veiculos(id) ON DELETE RESTRICT;


--
-- Name: turnos turnos_motorista_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.turnos
    ADD CONSTRAINT turnos_motorista_id_fkey FOREIGN KEY (motorista_id) REFERENCES public.motoristas(id) ON DELETE RESTRICT;


--
-- Name: turnos turnos_veiculo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.turnos
    ADD CONSTRAINT turnos_veiculo_id_fkey FOREIGN KEY (veiculo_id) REFERENCES public.veiculos(id) ON DELETE RESTRICT;


--
-- Name: veiculos veiculos_motorista_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.veiculos
    ADD CONSTRAINT veiculos_motorista_id_fkey FOREIGN KEY (motorista_id) REFERENCES public.motoristas(id) ON DELETE RESTRICT;


--
-- Name: assinaturas; Type: ROW SECURITY; Schema: public; Owner: admin
--

ALTER TABLE public.assinaturas ENABLE ROW LEVEL SECURITY;

--
-- Name: caixas_provisao; Type: ROW SECURITY; Schema: public; Owner: admin
--

ALTER TABLE public.caixas_provisao ENABLE ROW LEVEL SECURITY;

--
-- Name: despesas_fixas_mensais; Type: ROW SECURITY; Schema: public; Owner: admin
--

ALTER TABLE public.despesas_fixas_mensais ENABLE ROW LEVEL SECURITY;

--
-- Name: dlq_eventos; Type: ROW SECURITY; Schema: public; Owner: admin
--

ALTER TABLE public.dlq_eventos ENABLE ROW LEVEL SECURITY;

--
-- Name: fechamento_diario; Type: ROW SECURITY; Schema: public; Owner: admin
--

ALTER TABLE public.fechamento_diario ENABLE ROW LEVEL SECURITY;

--
-- Name: assinaturas isolamento_assinaturas; Type: POLICY; Schema: public; Owner: admin
--

CREATE POLICY isolamento_assinaturas ON public.assinaturas USING ((motorista_id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: caixas_provisao isolamento_caixas; Type: POLICY; Schema: public; Owner: admin
--

CREATE POLICY isolamento_caixas ON public.caixas_provisao USING ((motorista_id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: despesas_fixas_mensais isolamento_despesas; Type: POLICY; Schema: public; Owner: admin
--

CREATE POLICY isolamento_despesas ON public.despesas_fixas_mensais USING ((motorista_id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: despesas_fixas_mensais isolamento_despesas_fixas; Type: POLICY; Schema: public; Owner: admin
--

CREATE POLICY isolamento_despesas_fixas ON public.despesas_fixas_mensais USING ((motorista_id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: dlq_eventos isolamento_dlq; Type: POLICY; Schema: public; Owner: admin
--

CREATE POLICY isolamento_dlq ON public.dlq_eventos USING ((motorista_id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: fechamento_diario isolamento_fechamento; Type: POLICY; Schema: public; Owner: admin
--

CREATE POLICY isolamento_fechamento ON public.fechamento_diario USING ((motorista_id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: lgpd_logs isolamento_lgpd; Type: POLICY; Schema: public; Owner: admin
--

CREATE POLICY isolamento_lgpd ON public.lgpd_logs USING ((motorista_id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: motoristas isolamento_motoristas; Type: POLICY; Schema: public; Owner: admin
--

CREATE POLICY isolamento_motoristas ON public.motoristas USING ((id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: transacoes isolamento_transacoes; Type: POLICY; Schema: public; Owner: admin
--

CREATE POLICY isolamento_transacoes ON public.transacoes USING ((motorista_id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: turnos isolamento_turnos; Type: POLICY; Schema: public; Owner: admin
--

CREATE POLICY isolamento_turnos ON public.turnos USING ((motorista_id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: veiculos isolamento_veiculos; Type: POLICY; Schema: public; Owner: admin
--

CREATE POLICY isolamento_veiculos ON public.veiculos USING ((motorista_id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: lgpd_logs; Type: ROW SECURITY; Schema: public; Owner: admin
--

ALTER TABLE public.lgpd_logs ENABLE ROW LEVEL SECURITY;

--
-- Name: motoristas; Type: ROW SECURITY; Schema: public; Owner: admin
--

ALTER TABLE public.motoristas ENABLE ROW LEVEL SECURITY;

--
-- Name: transacoes; Type: ROW SECURITY; Schema: public; Owner: admin
--

ALTER TABLE public.transacoes ENABLE ROW LEVEL SECURITY;

--
-- Name: turnos; Type: ROW SECURITY; Schema: public; Owner: admin
--

ALTER TABLE public.turnos ENABLE ROW LEVEL SECURITY;

--
-- Name: veiculos; Type: ROW SECURITY; Schema: public; Owner: admin
--

ALTER TABLE public.veiculos ENABLE ROW LEVEL SECURITY;

--
-- PostgreSQL database dump complete
--

\unrestrict dJXcMxHn5FpQjDSNUlwaLLYUiQdsFreKc5aGKa19vUOcxvUifxDwz1AMokryrKE

