# -*- coding: utf-8 -*-
"""driver/pipeline.py —— 管道与阶段类（阶段三落地）。

目标（docs/hadoop/driver-refactor-plan.md §3/§4/§6 阶段三）：
  * 阶段序列由 `build_stages()` 一处给出，不再有两份定义（旧 STAGES 常量与
    run_cluster() 手写顺序靠人肉保持一致的问题在此根治）；
  * 模式判断收敛到 `select_pipeline(ctx)` 一处；
  * 各阶段 = 一条消息一个类，作业链语义可读。

迁移纪律（阶段三红线：行为不变）：
  * 本文件的 Stage 内容 == 旧 `Runner.run_cluster / run_local` 逐行搬入，
    调用点从 `self.xxx` 改为 `ctx.xxx`（ctx 代理到 Runner，见 TaskContext）；
  * `Runner` 的实现方法（job/fetch/_score/publish/…）暂不搬家 —— 阶段四再迁
    hdfsio.py / task.py / report.py，届时 Stage 无需改动（它们只依赖 ctx）；
  * 阶段标记顺序与原来一致：finalize 标记仍由 finish() 打（在 PublishStage
    之前），publish 标记在 PublishStage 内打，done 标记由 finish() 收尾打。

TaskContext（拍板 #4 第一步落地）：阶段类的唯一依赖面 —— 把 Runner 当服务
容器代理（__getattr__ 透传），并显式携带仓库根供脚本路径使用。
"""

import os

from driver import run_task as rt

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TaskContext(object):
    """各层共享上下文：阶段类经 ctx 访问 Runner 的服务（提交作业/取回/计数…）
    与元数据（task_id/路径/schemes/mode/scope）。

    现阶段是 Runner 的服务视图：读经 __getattr__ 代理，写经 __setattr__ 落到
    Runner 上（阶段类写 ctx.loc_stats / ctx.published，Runner.finish() 原样可读）；
    阶段四把实现迁入独立模块时，Stage 的调用面（ctx.<方法>）保持不变。
    """

    def __init__(self, runner):
        self._runner = runner

    def __getattr__(self, name):
        return getattr(self._runner, name)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._runner, name, value)


class Stage(object):
    """一个可执行阶段；execute_stage(ctx) 是唯一入口。"""

    def execute_stage(self, ctx):
        raise NotImplementedError("子类必须实现 execute_stage")


class UploadStage(Stage):
    """集群专属：原始数据上传 HDFS（full 全量 / sample 取 2000 行）。"""

    def execute_stage(self, ctx):
        # D-016：默认**全量**（正式口径，约 8 分钟）；样本（--scope sample）仅联调用。
        rt.run_shell(["bash", os.path.join(REPO_ROOT, "hadoop", "scripts",
                                           "upload_raw.sh")]
                     + ([] if ctx.scope == "full" else ["--sample", "2000"]),
                     os.path.join(ctx.task_dir, "logs", "upload_raw.log"))


class CleanUsersStage(Stage):
    def execute_stage(self, ctx):
        # D-014：双模式两趟合并为一趟（K/Q 标签流），隔离区由收尾阶段从
        # 各表最后一道清洗作业的输出里按标签分拣出来。
        ctx.stage("clean_users")
        ctx.job("users_normalize.py", "%s/users.dat" % ctx.raw_hdfs, "%s/u_norm" % ctx.hdfs)
        ctx.job("users_resolve.py", "%s/u_norm" % ctx.hdfs, "%s/u_res" % ctx.hdfs, reduces=1)
        ctx.job("clean_finalize.py", "%s/u_res" % ctx.hdfs, "%s/u_final" % ctx.hdfs,
                reduces=1, mapper_args="--table users", reducer_args="--table users")


class CleanMoviesStage(Stage):
    def execute_stage(self, ctx):
        ctx.stage("clean_movies")
        ctx.job("movies_normalize.py", "%s/movies.dat" % ctx.raw_hdfs, "%s/m_norm" % ctx.hdfs)
        ctx.job("movies_resolve.py", "%s/m_norm" % ctx.hdfs, "%s/m_res" % ctx.hdfs, reduces=1)
        ctx.job("movies_residual.py", "%s/m_res" % ctx.hdfs, "%s/m_resid" % ctx.hdfs)
        ctx.job("clean_finalize.py", "%s/m_resid" % ctx.hdfs, "%s/m_final" % ctx.hdfs,
                reduces=1, mapper_args="--table movies", reducer_args="--table movies")


class CleanRatingsStage(Stage):
    def execute_stage(self, ctx):
        ctx.stage("clean_ratings")
        ctx.job("ratings_validate.py", "%s/ratings.dat" % ctx.raw_hdfs, "%s/r_val" % ctx.hdfs)
        ctx.job("ratings_dedupe.py", "%s/r_val" % ctx.hdfs, "%s/r_ded" % ctx.hdfs, reduces=1)
        dims = ctx._dim_files()
        ctx.job("ratings_cross.py", "%s/r_ded" % ctx.hdfs, "%s/r_cross" % ctx.hdfs,
                mapper_args="--users users_dim.jsonl --movies movies_dim.jsonl",
                extra_files="%s,%s" % (dims["users"], dims["movies"]))
        ctx.job("clean_finalize.py", "%s/r_cross" % ctx.hdfs, "%s/r_final" % ctx.hdfs,
                reduces=1, mapper_args="--table ratings", reducer_args="--table ratings")


class StatsStage(Stage):
    def execute_stage(self, ctx):
        ctx.stage("stats_marks")
        ctx.job("stats_marks.py", "%s/ratings.dat" % ctx.raw_hdfs, "%s/stats_raw" % ctx.hdfs,
                reduces=1, mapper_args="--source raw-ratings",
                reducer_args="--source raw-ratings")
        r9 = os.path.join(ctx.task_dir, "r9_users.json")
        ctx.fetch("%s/stats_raw" % ctx.hdfs, r9)
        ctx.job("stats_marks.py", ["%s/r_cross" % ctx.hdfs, "%s/u_res" % ctx.hdfs,
                                   "%s/m_resid" % ctx.hdfs],
                "%s/stats_clean" % ctx.hdfs, reduces=1,
                mapper_args="--source cleaned --r9-users r9_users.json",
                reducer_args="--source cleaned", extra_files=r9)


class ScoreStage(Stage):
    """一侧评分：side="before" 用原始维表，side="after" 用清洗后维表。"""

    def __init__(self, side):
        self.side = side          # "before" | "after"

    def execute_stage(self, ctx):
        if self.side == "before":
            ctx._score("score_before", "raw", [ctx.raw_hdfs])
        else:
            ctx._score("score_after", "cleaned", [ctx.hdfs])


class FinalizeStage(Stage):
    """交付格式的三表取回 + 隔离区分拣（D-014 的本地分拣步）。

    与旧 run_cluster 末尾一致：本阶段不打 finalize 标记（标记由 finish() 打）。
    """

    def execute_stage(self, ctx):
        cleaned = os.path.join(ctx.task_dir, "cleaned",
                               ctx.schemes.rules["data_version"]["id"])
        for table, src in (("users", "u_final"), ("movies", "m_final"),
                           ("ratings", "r_final")):
            ctx.fetch("%s/%s" % (ctx.hdfs, src),
                      os.path.join(cleaned, rt.TABLE_FILES[table]), prefix_table=table)
        ctx._assemble_quarantine(cleaned)


class PublishStage(Stage):
    """发布：cluster 真发（版本哈希保护 D-017），local no-op。

    publish 的 counts 形参在实现里未被使用（历史遗留），这里按旧调用传 {}
    —— 行为与旧 finish() 一致（local 返回 None 写入 published=None）。
    """

    def execute_stage(self, ctx):
        ctx.stage("publish")
        ctx.published = ctx.publish()


class LocalEngineStage(Stage):
    """local 后端：本地 runner 跑同一条链 + 补写九阶段进度（与旧 run_local 一致）。"""

    def execute_stage(self, ctx):
        from engine.pipeline import run_local as engine_run_local
        ctx.stage("clean_users")
        stats = engine_run_local(rt.raw_dir(), ctx.schemes, ctx.task_dir, ctx.tid,
                                 processed_at=rt.now_utc())
        for name in rt.STAGES[1:]:
            ctx.stage(name)
        ctx.loc_stats = stats


class Pipeline(object):
    """管道基类：阶段清单 + 顺序执行。"""

    def __init__(self, ctx):
        self.ctx = ctx

    def build_stages(self):
        raise NotImplementedError("子类必须给出阶段清单")

    def execute_all_stages(self):
        for stage in self.build_stages():
            stage.execute_stage(self.ctx)


class ClusterPipeline(Pipeline):
    """集群管道：上传 → 三表清洗 → 打标 → 双侧评分 → 取回分拣 → 发布。"""

    def build_stages(self):
        return [UploadStage(), CleanUsersStage(), CleanMoviesStage(),
                CleanRatingsStage(), StatsStage(), ScoreStage("before"),
                ScoreStage("after"), FinalizeStage(), PublishStage()]


class LocalPipeline(Pipeline):
    """本地管道：本地引擎跑全链（发布为 no-op，PublishStage 只打标记）。"""

    def build_stages(self):
        return [LocalEngineStage(), PublishStage()]


def select_pipeline(ctx):
    """按运行模式选择管道实现（模式判断的唯一入口）。"""
    if ctx.mode == "local":
        return LocalPipeline(ctx)
    return ClusterPipeline(ctx)