// Openshift project
OPENSHIFT_NAMESPACE = 'ai-coe'
OPENSHIFT_SERVICE_ACCOUNT = 'jenkins'
DOCKER_REPO_URL = 'docker-registry.default.svc.cluster.local:5000'

// Defaults for SCM operations
env.ghprbGhRepository = env.ghprbGhRepository ?: 'AICoE/thoth-dependency-monkey'
env.ghprbActualCommit = env.ghprbActualCommit ?: 'master'

// If this PR does not include an image change, then use this tag
STABLE_LABEL = "stable"
tagMap = [:]

// Initialize
tagMap['thoth-dependency-monkey'] = '0.1.2'
tagMap['pypi-validator'] = '0.1.2'

// IRC properties
IRC_NICK = "aicoe-bot"
IRC_CHANNEL = "#thoth-station"

properties(
    [
        buildDiscarder(logRotator(artifactDaysToKeepStr: '30', artifactNumToKeepStr: '', daysToKeepStr: '90', numToKeepStr: '')),
        disableConcurrentBuilds(),
    ]
)


library(identifier: "cico-pipeline-library@master",
        retriever: modernSCM([$class: 'GitSCMSource',
                              remote: "https://github.com/CentOS/cico-pipeline-library",
                              traits: [[$class: 'jenkins.plugins.git.traits.BranchDiscoveryTrait'],
                                       [$class: 'RefSpecsSCMSourceTrait',
                                        templates: [[value: '+refs/heads/*:refs/remotes/@{remote}/*']]]]])
                            )
library(identifier: "ci-pipeline@master",
        retriever: modernSCM([$class: 'GitSCMSource',
                              remote: "https://github.com/CentOS-PaaS-SIG/ci-pipeline",
                              traits: [[$class: 'jenkins.plugins.git.traits.BranchDiscoveryTrait'],
                                       [$class: 'RefSpecsSCMSourceTrait',
                                        templates: [[value: '+refs/heads/*:refs/remotes/@{remote}/*']]]]])
                            )
library(identifier: "ai-stacks-pipeline@master",
        retriever: modernSCM([$class: 'GitSCMSource',
                              remote: "https://github.com/goern/AI-Stacks-pipeline",
                              traits: [[$class: 'jenkins.plugins.git.traits.BranchDiscoveryTrait'],
                                       [$class: 'RefSpecsSCMSourceTrait',
                                        templates: [[value: '+refs/heads/*:refs/remotes/@{remote}/*']]]]])
                            )

pipeline {
    agent {
        kubernetes {
            cloud 'openshift'
            label 'ai-stacks-pipeline-' + env.ghprbActualCommit
            serviceAccount OPENSHIFT_SERVICE_ACCOUNT
            containerTemplate {
                name 'jnlp'
                args '${computer.jnlpmac} ${computer.name}'
                image DOCKER_REPO_URL + 'ai-coe/jenkins-ai-coe-slave:' + STABLE_LABEL
                ttyEnabled false
                command ''
            }
        }
    }
    stages {
        stage("Setup Build Templates") {
            steps {
                script {
                    aIStacksPipelineUtils.createBuildConfigs(OPENSHIFT_NAMESPACE)
                }
            }
        }
        stage("Get Changelog") {
            steps {
                node('master') {
                    script {
                        env.changeLogStr = pipelineUtils.getChangeLogFromCurrentBuild()
                        echo env.changeLogStr
                    }
                    writeFile file: 'changelog.txt', text: env.changeLogStr
                    archiveArtifacts allowEmptyArchive: true, artifacts: 'changelog.txt'
                }
            }
        }
        stage("Build Container Images") {
            parallel {
                stage("Thoth: Dependency Monkey") {
                    steps {
                        echo "Building Thoth Dependency Monkey container image..."
                        script {
                            tagMap['thoth-dependency-monkey'] = aIStacksPipelineUtils.buildImageWithTag(OPENSHIFT_NAMESPACE, "api-service", '0.1.2')
                        }

                        echo "Building PyPI Validator container image..."
                        script {
                            tagMap['pypi-validator'] = aIStacksPipelineUtils.buildImageWithTag(OPENSHIFT_NAMESPACE, "pypi-validator", '0.1.2')
                        }
                    }   
                } 
            }
        }
        stage("Run Tests") {
            failFast true
            parallel {
                stage("Functional Tests") {
                    steps {
                        sh 'pytest'
                    }
                }
            }
        }
        stage("Image Tag Report") {
            steps {
                script {
                    pipelineUtils.printLabelMap(tagMap)
                }
            }
        }
    }
    post {
        always {
            script {
                junit 'reports/*.xml'

                String prMsg = ""
                if (env.ghprbActualCommit != null && env.ghprbActualCommit != "master") {
                    prMsg = "(PR #${env.ghprbPullId} ${env.ghprbPullAuthorLogin})"
                }
                def message = "${JOB_NAME} ${prMsg} build #${BUILD_NUMBER}: ${currentBuild.currentResult}: ${BUILD_URL}"

                pipelineUtils.sendIRCNotification("${IRC_NICK}", IRC_CHANNEL, message)
                mattermostSend channel: "#thoth-station", icon: 'https://avatars1.githubusercontent.com/u/33906690', message: "${message}"

            }
        }
        success {
            echo "All Systems GO!"
        }
        failure {
            error "BREAK BREAK BREAK - build failed!"
        }
    }
}