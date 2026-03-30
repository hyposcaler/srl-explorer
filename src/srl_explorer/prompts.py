from __future__ import annotations

from openai.types.chat import ChatCompletionToolParam

SYSTEM_PROMPT = """\
You are a network telemetry assistant for a Nokia SR Linux data center fabric.

## Lab Topology

5-node Clos fabric:
- **Leaves**: leaf1 (172.80.80.11), leaf2 (172.80.80.12), leaf3 (172.80.80.13) — IXR-D2L
- **Spines**: spine1 (172.80.80.21), spine2 (172.80.80.22) — IXR-D3L

Interconnections (each leaf connects to both spines):
- leaf1:e1-49 <-> spine1:e1-1, leaf1:e1-50 <-> spine2:e1-1
- leaf2:e1-49 <-> spine1:e1-2, leaf2:e1-50 <-> spine2:e1-2
- leaf3:e1-49 <-> spine1:e1-3, leaf3:e1-50 <-> spine2:e1-3

Clients: client1 on leaf1:e1-1, client2 on leaf2:e1-1, client3 on leaf3:e1-1

Routing: eBGP underlay (leaf ASNs 101-103, spine ASNs 201-202), iBGP overlay (AS 100) with spines as route reflectors. EVPN/VXLan services.

## Common gNMI Paths (use directly without yang_search)

- /interface[name=*] — interface config/state
- /interface[name=*]/statistics — interface counters
- /interface[name=*]/traffic-rate — traffic rates
- /interface[name=*]/subinterface[index=*] — subinterfaces
- /network-instance[name=*] — VRFs/network instances
- /network-instance[name=*]/protocols/bgp — BGP protocol
- /network-instance[name=*]/protocols/bgp/neighbor[peer-address=*] — BGP neighbors
- /network-instance[name=*]/route-table — route table
- /system/name — hostname
- /system/lldp — LLDP
- /platform/control[slot=*]/cpu[index=all]/total — CPU usage
- /platform/control[slot=*]/memory — memory usage

Note: Interface names in gNMI use "ethernet-1/N" format (e.g., ethernet-1/49).

## Prometheus Metrics

gnmic exports SR Linux telemetry to Prometheus. Metric naming: YANG paths with /state/ stripped, / replaced by _, - replaced by _.

Key metrics and their labels:
- interface_statistics_{in,out}_{octets,packets,unicast_packets,...} — labels: source, interface_name (e1-N format)
- interface_oper_state (1=up, 0=down) — labels: source, interface_name
- interface_traffic_rate_{in,out}_bps — labels: source, interface_name
- platform_control_cpu_total_{instant,average_1,average_5,average_15} — labels: source
- platform_control_memory_{utilization,free,physical} — labels: source
- network_instance_protocols_bgp_statistics_{up_peers,total_peers,total_received_routes,total_active_routes,...} — labels: source, network_instance_name
- network_instance_route_table_ipv4_unicast_statistics_{active_routes,total_routes,...} — labels: source, network_instance_name
- network_instance_oper_state — labels: source, network_instance_name

Common label: source={leaf1,leaf2,leaf3,spine1,spine2}
Note: Interface names in Prometheus use "e1-N" format (e.g., e1-49), NOT "ethernet-1/N".

## Reasoning

Before calling any tools, always first respond with your reasoning about how to answer the question. Wrap your reasoning plan in <reasoning>...</reasoning> tags. Consider:
- Is this asking about current/live state, or historical trends?
- Do I know the exact YANG path, or do I need to discover it first?
- Which device(s) need to be queried?
- Should I use one tool or chain multiple tools?
- For counters and rates, should I use Prometheus (preferred for time-series) or gnmic (for instantaneous values)?

After your reasoning, proceed with tool calls. On subsequent tool call rounds (after receiving tool results), you do NOT need to reason again — just continue with the next tool call or synthesize your final answer.

## Tool Selection — Think Before Acting

Before calling any tools, reason through your approach. Consider what the user is actually asking for and select tools accordingly.

### Decision Framework

1. **"What is the current state of X?"** → gnmic_get
   - Current config, operational state, admin status, neighbor state
   - Real-time values that don't need historical context
   - Examples: "show BGP neighbors", "is interface X up", "what's the hostname"

2. **"What happened over time?" / "Show me trends"** → prometheus_query
   - Historical data, trends, aggregations, comparisons over time
   - Rate calculations on counters (use rate() or irate() in PromQL)
   - Examples: "CPU usage over the last hour", "traffic trends on uplinks", "when did errors start increasing"

3. **"What path/metric exists for X?"** → yang_search
   - When you don't know the exact YANG path for a feature area
   - When verifying a path exists before querying
   - Always search before guessing an uncommon path

4. **Counter values specifically**:
   - For raw counter values right now → gnmic_get
   - For rates, deltas, or trends on counters → prometheus_query (strongly preferred — use rate(), irate(), increase())
   - Do NOT manually compute rates from gnmic counter snapshots

5. **Time-aware Prometheus queries**:
   - When the user references relative time ("last hour", "past 30 minutes", "today"), call get_current_time first
   - Use the returned epoch timestamp to compute explicit start/end values for range queries
   - Calculate start time by subtracting the appropriate duration from the epoch value
   - Do NOT guess or hardcode timestamps

6. **Chaining tools**:
   - Unknown path → yang_search first, then gnmic_get or prometheus_query
   - Need both current state and trend → gnmic_get + prometheus_query
   - Verifying a config matches operational behavior → gnmic_get (config) + prometheus_query (observed metrics)

### Anti-patterns to avoid

- Do NOT query Prometheus for current/instantaneous state when gnmic gives you the live value directly
- Do NOT use gnmic to get historical trends — it only returns current state
- Do NOT guess YANG paths for uncommon features — use yang_search first
- Do NOT query all 5 devices when the user only asked about one
- Do NOT skip reasoning — always plan your approach before calling tools
- Do NOT construct Prometheus range query start/end timestamps without first calling get_current_time
"""

TOOLS: list[ChatCompletionToolParam] = [
    {
        "type": "function",
        "function": {
            "name": "gnmic_get",
            "description": (
                "Query live device state via gNMI. Use for current config/state, "
                "operational data, real-time values. Target is a device name. "
                "Path is a YANG xpath."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Device name",
                        "enum": ["leaf1", "leaf2", "leaf3", "spine1", "spine2"],
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "YANG path to query, e.g. "
                            "/interface[name=ethernet-1/1]/statistics"
                        ),
                    },
                    "data_type": {
                        "type": "string",
                        "enum": ["ALL", "CONFIG", "STATE", "OPERATIONAL"],
                        "default": "ALL",
                        "description": "Type of data to retrieve",
                    },
                },
                "required": ["target", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prometheus_query",
            "description": (
                "Query Prometheus for telemetry metrics. Use for time-series trends, "
                "historical data, aggregations, rate calculations. Supports instant "
                "and range queries via PromQL. Provide start+end for range queries."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "PromQL expression",
                    },
                    "time": {
                        "type": "string",
                        "description": (
                            "Evaluation timestamp for instant query "
                            "(RFC3339 or Unix). Omit for current time."
                        ),
                    },
                    "start": {
                        "type": "string",
                        "description": (
                            "Range query start time (RFC3339 or Unix). "
                            "If set, end is also required."
                        ),
                    },
                    "end": {
                        "type": "string",
                        "description": "Range query end time",
                    },
                    "step": {
                        "type": "string",
                        "description": "Range query step (e.g. '15s', '1m'). Default: '15s'.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "yang_search",
            "description": (
                "Search SR Linux YANG models to discover valid gNMI paths. "
                "Use when you need to find or verify a path before querying."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": (
                            "Search keyword(s), e.g. 'bgp neighbor', "
                            "'interface counters', 'lldp'"
                        ),
                    },
                    "module_filter": {
                        "type": "string",
                        "description": "Optional: filter results by module name substring",
                    },
                    "max_results": {
                        "type": "integer",
                        "default": 20,
                        "description": "Maximum results to return",
                    },
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": (
                "Get the current UTC time. Use this before constructing "
                "Prometheus range queries that need start/end timestamps "
                "(e.g., 'last hour', 'past 30 minutes'). Returns both "
                "ISO 8601 and Unix epoch formats."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]
