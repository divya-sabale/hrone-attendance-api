# DECISIONS.md

1. **Indexes.** employees: unique emp_code (no duplicate codes), and (department, joined_on) plus joined_on (department filter and headcount). attendance_logs: unique (emp_code, date) (one record per day, monthly summary), (date -1, emp_code 1) (GET /attendance sort), and (date 1, emp_code 1) (leaderboard and trend date ranges). I rejected an index on status, because it has only five values and is not selective.

2. **Punch-in race.** Both requests call insert_one. The unique index on (emp_code, date) lets MongoDB accept only one of them. The other gets DuplicateKeyError and I return 409. So exactly one request gets 201.

3. **Ties.** I rank with $rank and then keep rank <= limit. Tied employees share a rank, so both are returned even if that gives more rows than limit. Rows are ordered by late minutes, then emp_code.

4. **Headcount.** The department summary starts from the employees collection (joined_on <= end of month) and uses $lookup for the logs. So employees with no logs are counted, with zero days.

5. **100x data.** I would keep monthly totals in a separate collection, updated on punch-in, punch-out and correction, instead of computing them from raw logs on every request.