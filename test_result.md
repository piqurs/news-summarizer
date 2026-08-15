#====================================================================================================
# START - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================

# THIS SECTION CONTAINS CRITICAL TESTING INSTRUCTIONS FOR BOTH AGENTS
# BOTH MAIN_AGENT AND TESTING_AGENT MUST PRESERVE THIS ENTIRE BLOCK

# Communication Protocol:
# If the `testing_agent` is available, main agent should delegate all testing tasks to it.
#
# You have access to a file called `test_result.md`. This file contains the complete testing state
# and history, and is the primary means of communication between main and the testing agent.
#
# Main and testing agents must follow this exact format to maintain testing data. 
# The testing data must be entered in yaml format Below is the data structure:
# 
## user_problem_statement: {problem_statement}
## backend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.py"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## frontend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.js"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## metadata:
##   created_by: "main_agent"
##   version: "1.0"
##   test_sequence: 0
##   run_ui: false
##
## test_plan:
##   current_focus:
##     - "Task name 1"
##     - "Task name 2"
##   stuck_tasks:
##     - "Task name with persistent issues"
##   test_all: false
##   test_priority: "high_first"  # or "sequential" or "stuck_first"
##
## agent_communication:
##     -agent: "main"  # or "testing" or "user"
##     -message: "Communication message between agents"

# Protocol Guidelines for Main agent
#
# 1. Update Test Result File Before Testing:
#    - Main agent must always update the `test_result.md` file before calling the testing agent
#    - Add implementation details to the status_history
#    - Set `needs_retesting` to true for tasks that need testing
#    - Update the `test_plan` section to guide testing priorities
#    - Add a message to `agent_communication` explaining what you've done
#
# 2. Incorporate User Feedback:
#    - When a user provides feedback that something is or isn't working, add this information to the relevant task's status_history
#    - Update the working status based on user feedback
#    - If a user reports an issue with a task that was marked as working, increment the stuck_count
#    - Whenever user reports issue in the app, if we have testing agent and task_result.md file so find the appropriate task for that and append in status_history of that task to contain the user concern and problem as well 
#
# 3. Track Stuck Tasks:
#    - Monitor which tasks have high stuck_count values or where you are fixing same issue again and again, analyze that when you read task_result.md
#    - For persistent issues, use websearch tool to find solutions
#    - Pay special attention to tasks in the stuck_tasks list
#    - When you fix an issue with a stuck task, don't reset the stuck_count until the testing agent confirms it's working
#
# 4. Provide Context to Testing Agent:
#    - When calling the testing agent, provide clear instructions about:
#      - Which tasks need testing (reference the test_plan)
#      - Any authentication details or configuration needed
#      - Specific test scenarios to focus on
#      - Any known issues or edge cases to verify
#
# 5. Call the testing agent with specific instructions referring to test_result.md
#
# IMPORTANT: Main agent must ALWAYS update test_result.md BEFORE calling the testing agent, as it relies on this file to understand what to test next.

#====================================================================================================
# END - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================



#====================================================================================================
# Testing Data - Main Agent and testing sub agent both should log testing data below this section
#====================================================================================================
user_problem_statement: >
  Extend the existing Latest Updates implementation (news-summarize branch) per
  "Latest Update Pipeline Design.txt": (1) build_queries — up to 8 search query
  variants run CONCURRENTLY via asyncio.gather+to_thread; (2) web_fetch — full-page
  text for top ~8 candidates with mandatory snippet fallback, 8s timeout;
  (3) cluster_by_date — group candidates by publish date before LLM synthesis;
  (4) compute_confidence — deterministic 3-factor score in code (>=4 domains /
  >=50% events corroborated by >=2 domains / newest used candidate <48h;
  score>=2 High, 1 Medium, 0 Low); (5) merge_timeline — persistent accumulated
  timeline in separate timeline_store collection, Jaccard>=0.5 same-day dedup,
  timeline never shrinks. key_entities now stored in summary payload (no extra
  LLM entity-extraction call).

backend:
  - task: "Latest Updates pipeline (build_queries, search_candidates concurrent, web_fetch_candidates, cluster_by_date, synthesize_delta, compute_confidence, merge_timeline, timeline_store persistence)"
    implemented: true
    working: true
    file: "backend/services.py, backend/server.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
      - working: true
        agent: "main"
        comment: >
          Manually verified end-to-end: 8 concurrent Tavily queries in 3-6s
          (0 failed), web_fetch 3/8 full pages rest snippet-fallback, timeline
          accumulated 0->4->7->8 across refreshes (never shrank), delta cached
          response instant, confidence computed deterministically (score 2/3
          High -> 1/3 Medium across runs), timeline_store persisted. Total
          pipeline 34s (LLM generation dominates; search+fetch ~8s combined).
      - working: true
        agent: "testing"
        comment: >
          Comprehensive automated testing completed successfully (9/9 tests passed).
          Verified: (1) GET /api/health returns 200 OK; (2) POST /api/summarize 
          returns valid summary with non-empty key_entities array; (3) POST 
          /api/latest-updates first call completes in 31.8s with valid response 
          schema (has_update, overview, developments, timeline, current_situation, 
          market_impact, confidence with level/reason/score/factors, sources_used); 
          (4) Confidence scoring is deterministic: score 1/3 → Medium (factors: 
          2 domains <4, 0% corroboration <50%, newest <48h=true); all URLs in 
          sources_used and timeline[].sources are valid http(s) URLs; (5) Second 
          POST returns cached response instantly (0.3s); (6) Timeline accumulation 
          verified: forced fresh run increased timeline from 8→11 events (never 
          shrinks), timeline_store collection persisted correctly; (7) Backend logs 
          confirm concurrent execution: "8 queries in 1.9-5.8s" (all <10s), 
          "web_fetch 3/8 full pages" with snippet fallback; (8) Edge case with 
          nonsense topic returns 200 with has_update=false (no crash); (9) Rate 
          limit enforced: 5 calls succeeded, 6th returned 429 with Retry-After 
          header. All critical requirements met.

metadata:
  created_by: "main_agent"
  version: "1.1"
  test_sequence: 1
  run_ui: false

test_plan:
  current_focus:
    - "Latest Updates pipeline"
  stuck_tasks: []
  test_all: false
  test_priority: "high_first"

agent_communication:
  - agent: "main"
    message: >
      New pipeline live at POST /api/latest-updates {topic, source_url}.
      Response schema unchanged for frontend: {has_update, overview,
      developments[], timeline[{date,event,sources[]}], current_situation,
      market_impact, confidence{level,reason,score,factors}, sources_used[]}.
      Note: rate limit 5 updates/hour/IP (bucket "updates") — testing agent
      should budget calls or clear db.rate_limit between tests. Delta cached
      12h per article in summary_cache.latest_update_delta; accumulated
      timeline in db.timeline_store (no TTL). To force fresh pipeline run:
      unset latest_update_delta/delta_generated_at in summary_cache.
      /summarize must be called first for baseline entities (key_entities now
      in payload). Backend on localhost:8001, external URL in frontend/.env.
  - agent: "testing"
    message: >
      Automated testing completed successfully. All 9 test cases passed:
      health check, summarize with key_entities, latest-updates first call
      (31.8s with valid schema), deterministic confidence scoring (score→level
      mapping verified), cached response (instant), timeline accumulation
      (8→11 events, never shrinks), concurrent execution confirmed in logs
      (8 queries in <10s, web_fetch 3/8 full pages), edge case handling
      (nonsense topic returns 200), and rate limit enforcement (5 calls OK,
      6th returns 429 with Retry-After). Backend implementation is production-ready.
      No issues found. Main agent can summarize and finish.
