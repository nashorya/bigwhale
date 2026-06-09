# 上岸 待确认问题

> 遇到文档描述模糊或需要设计选择的问题，记录于此，不自行假设。

---

- [x] [scheduler] 科目时间分配比例：v3.1 文档提到 `subject_ratio` 由 Manager 动态计算，但 Manager 尚未实现。当前 scheduler 使用均分策略（每科平均分配时间），后续 Manager 实现后是否需要改为加权分配？
  - **结论**：均分策略先用着，等 Manager 实现后再切换加权分配。不阻塞进度。

- [x] [scheduler] `daily_capacity`（每日可学习总分钟数，默认 360）：v3.1 文档提到此参数由"用户设置"，但 init.sql 中无对应字段。需要新增到哪个表？还是暂时使用默认值？
  - **结论**：暂时写死默认值 360，不新增表。等后续有设置指令需求时再加字段。

- [x] [scheduler] 定时任务回调机制：当前 `register_scheduled_jobs` 注册的是占位回调，handler 层需要替换为实际回调。替换方式有两种：① 在 handler 层注册覆盖（`replace_existing=True`）；② 改为事件驱动（scheduler 发出信号，handler 监听）。用哪种方式？
  - **结论**：用方案①，handler 层启动时注册真实回调，`replace_existing=True` 覆盖占位函数。

- [x] [emotion_detector] 情绪熔断判定：v3.0 文档写"连续 3 次推词正确率 < 50%"，但当前推词系统未实现，无法精确统计"最近 3 次推词正确率"。当前简化为检查 `user_word_status` 中高权重错词数 ≥5。推词系统实现后是否需要细化？
  - **结论**：情绪熔断判定高权重错词数 ≥5 的简化实现先用着，推词系统实现后再细化。在 QUESTIONS.md 里标记为待推词系统完成后处理。

- [x] [emotion_detector] "长时间离线"检测：v3.0 文档写"计划学习时段内 > 90 分钟无任何消息"，但 core/ 层无法感知用户最后消息时间。需要 handler 层记录 `last_message_at` 到某个表或内存。应该存在哪里？
  - **结论**：存 UserSession 的内存里，handler 层每次收到消息时更新 session.last_message_at = datetime.now()，然后 check_system_anomaly 通过回调注入方式获取这个值，和 load_persona_fn 的设计一样。不需要落库。

- [x] [system handler] NoneBot2 多轮对话：当前使用 `matcher.pause()` + 多个 `@init_wizard.handle()` 链式处理 4 步向导。NoneBot2 文档中也有 `got()` 装饰器模式。如果用户在向导过程中发了其他指令（如 `#帮助`），当前实现可能导致状态混乱。是否需要加 session 超时或指令冲突检测？
  - **结论**：已实现。添加 `_wizard_guard` 守卫函数，5分钟超时自动退出，向导期间收到非预期 `#` 指令提示用户完成当前步骤或发 `#取消` 退出。

- [ ] [words handler] `#同步` CSV 文件上传：v3.1 文档提到支持用户上传 CSV 批量导入知识点。NoneBot2 v11 接收文件需要处理 `FileSegment` 类型消息，解析 CSV 并写入 `knowledge_points` 表。当前实现为 Stub，后续需要：①处理文件消息接收；②CSV 格式校验（科目名称, 知识点名称, 重要程度）；③批量 INSERT 并返回导入结果摘要。优先级：中，等推词系统完成后实现。

- [ ] [emotion handler] `emotion_scripts.in_session` 脚本 key：`handlers/emotion.py` 中被动监听和 `#陪我聊` 已在陪伴模式时尝试渲染 `emotion_scripts.in_session`，需确认各角色 JSON 文件（lingqi/baiquan/suwan/jilv）中是否定义了该 key。若未定义，PersonaEngine 会 fallback 到 `_fallback_render`，用户体验降级但不会报错。建议在各角色 JSON 中补充该 key。

